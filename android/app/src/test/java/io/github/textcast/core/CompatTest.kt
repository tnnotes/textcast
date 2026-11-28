package io.github.textcast.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * Byte-for-byte compatibility with the Python apps. vectors.json is made by
 * android/compat/make_vectors.py; regenerate it after protocol changes.
 * Set -Dtextcast.compat.out=DIR to also write Kotlin-made files that
 * android/compat/check_kotlin.py opens with Python.
 */
@Suppress("UNCHECKED_CAST")
class CompatTest {
    private val v: Map<String, Any?> = Json.parseObject(
        CompatTest::class.java.classLoader!!.getResource("vectors.json")!!.readText())
    private val t = (v["t"] as Number).toDouble()

    private fun list(key: String) = v[key] as List<Map<String, Any?>>
    private fun hx(m: Map<String, Any?>, k: String) = Bytes.unhex(m[k] as String)

    @Test fun chacha20Rfc8439() {
        // RFC 8439 section 2.4.2
        val key = ByteArray(32) { it.toByte() }
        val nonce = Bytes.unhex("000000000000004a00000000")
        val pt = Bytes.utf8("Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.")
        val ct = Crypto.chacha20Xor(key, nonce, pt, 1)
        assertEquals("6e2e359a2568f98041ba0728dd0d6981e97e7aec1d4360c20a27afccfd9fae0b", Bytes.hex(ct).substring(0, 64))
    }

    @Test fun ed25519() {
        for (x in list("ed25519")) {
            val seed = hx(x, "seed")
            assertArrayEquals(hx(x, "pub"), Ed25519.publicKey(seed))
            assertArrayEquals(hx(x, "sig"), Ed25519.sign(seed, hx(x, "msg")))
            assertTrue(Ed25519.verify(hx(x, "pub"), hx(x, "msg"), hx(x, "sig")))
            assertFalse(Ed25519.verify(hx(x, "pub"), Bytes.concat(hx(x, "msg"), byteArrayOf(1)), hx(x, "sig")))
        }
    }

    @Test fun scrypt() {
        for (x in list("scrypt")) {
            val out = Scrypt.derive(hx(x, "pass"), hx(x, "salt"), (x["n"] as Number).toInt(), (x["r"] as Number).toInt(),
                (x["p"] as Number).toInt(), (x["dklen"] as Number).toInt())
            assertEquals(x["out"], Bytes.hex(out))
        }
    }

    @Test fun encodings() {
        for (x in list("b32")) {
            assertEquals(x["b32"], Bytes.b32(hx(x, "bytes")))
            assertArrayEquals(hx(x, "bytes"), Bytes.unb32(x["b32"] as String))
        }
        for (x in list("fingerprint")) {
            assertEquals(x["fp"], Ids.fingerprint(hx(x, "pub")))
            assertEquals(x["handle"], Ids.makeHandle("Tuan", hx(x, "pub")))
        }
        for (x in list("derive")) assertEquals(x["out"], Bytes.hex(Crypto.derive(hx(x, "secret"), x["label"] as String)))
        for (x in list("member_secret")) assertEquals(x["out"], Bytes.hex(Crypto.memberSecret(hx(x, "gs"), x["cs"] as String)))
        val sample = Bytes.random(37)
        assertArrayEquals(sample, Bytes.unb64(Bytes.b64(sample)))
    }

    @Test fun frames() {
        for (x in list("frames")) {
            val k = Crypto.StreamKey(hx(x, "secret"))
            val i = (x["i"] as Number).toLong()
            val mode = (x["mode"] as Number).toInt()
            val enc = x["encrypt"] as Boolean
            val frame = Crypto.seal(k, mode, i, hx(x, "text"), enc)
            assertEquals(x["frame"], Bytes.hex(frame))
            assertEquals(i, Crypto.findCounter(k, frame, 0, t))
            assertArrayEquals(hx(x, "text"), Crypto.unseal(k, frame, i, enc))
        }
        for (x in list("candidates")) {
            val hint = (x["hint"] as Number?)?.toInt()
            val got = Crypto.candidates((x["last_i"] as Number).toLong(), t, hint).take(40).toList()
            assertEquals((x["first"] as List<Number>).map { it.toLong() }, got)
        }
    }

    @Test fun profileMaths() {
        for (x in list("airtime")) {
            val p = AirProfile(sf = (x["sf"] as Number).toInt(), bwHz = (x["bw"] as Number).toInt(), cr = (x["cr"] as Number).toInt())
            assertEquals((x["ms"] as Number).toDouble(), Protocol.airtimeMs((x["len"] as Number).toInt(), p, 1), 1e-9)
        }
        for (x in list("effective")) {
            val hw = Hardware.load(x["hw"] as String)
            Protocol.setHardwareLimits(hw.maxPayload, hw.firmwareOverhead)
            val pr = Regions.PRESETS[x["region"] as String]!!
            val p = AirProfile(sf = (x["sf"] as Number).toInt(), bwHz = pr.bwHz, maxDwellMs = pr.maxDwellMs)
            assertEquals("${x["hw"]} ${x["region"]} SF${x["sf"]}", (x["eff"] as Number).toInt(), Protocol.effectivePayload(p))
            (x["build"] as Number?)?.let { assertEquals(it.toInt(), Settings.buildProfile(mapOf("region" to x["region"])).maxPayload) }
        }
        val la66 = Hardware.load(null)
        Protocol.setHardwareLimits(la66.maxPayload, la66.firmwareOverhead)
        for (x in list("builds")) {
            val p = Settings.buildProfile(x["radio"] as Map<String, Any?>)
            assertEquals(x["name"], p.name)
            assertEquals((x["freq"] as Number).toDouble(), p.freqMhz, 1e-9)
            assertEquals((x["sf"] as Number).toInt(), p.sf)
            assertEquals((x["bw"] as Number).toInt(), p.bwHz)
            assertEquals((x["power"] as Number).toInt(), p.txPowerDbm)
            assertEquals((x["dwell"] as Number?)?.toDouble(), p.maxDwellMs)
            assertEquals((x["duty"] as Number?)?.toDouble(), p.dutyCycle)
            assertEquals((x["max"] as Number).toInt(), p.maxPayload)
        }
    }

    @Test fun plainAndDecode() {
        Protocol.setHardwareLimits(Hardware.load(null).maxPayload, 1)
        Protocol.applyProfile(AirProfile())
        for (x in list("plain")) assertEquals(x["frame"], Bytes.hex(Protocol.encodePlain(Protocol.compose(x["text"] as String, x["cs"] as String?))))
        for (x in list("decode")) {
            val d = Protocol.decode(hx(x, "frame"))
            assertEquals(x["kind"], d.kind)
            assertEquals(x["text"], d.text)
            assertEquals(x["callsign"], d.callsign)
            assertEquals(x["tokens"], d.tokens)
            assertEquals(x["display"], d.display())
        }
    }

    @Test fun hardwareAndParser() {
        Protocol.setHardwareLimits(Hardware.load(null).maxPayload, 1)
        val p = Settings.buildProfile(mapOf("region" to "AS923-2"))
        assertEquals(v["la66_at"], La66Parser.atCommands(p))
        for ((name, r) in v["render"] as Map<String, Map<String, Any?>>) {
            val hw = Hardware.load(name)
            assertEquals(name, r["init"], Hardware.renderAll(hw.init, p))
            assertEquals(name, r["rx_start"], Hardware.renderAll(hw.rxStart, p))
            if (hw.send.isNotEmpty()) assertEquals(name, r["send"], Hardware.render(hw.send, p, Bytes.unhex("01ab"), hw.hexUpper))
            assertTrue(hw.validate().toString(), hw.validate().isEmpty())
        }
        for (x in list("la66_parse")) {
            val got = La66Parser.parse(x["line"] as String).map { e ->
                when (e) {
                    is La66Parser.Ev.Banner -> listOf("banner", e.key, e.value)
                    is La66Parser.Ev.RxStart -> listOf("rx_start")
                    is La66Parser.Ev.RxData -> listOf("rx_data", Bytes.hex(e.data))
                    is La66Parser.Ev.Rssi -> listOf("rssi", e.v.toLong())
                    is La66Parser.Ev.Recv -> listOf("recv", Bytes.hex(e.data), e.rssi.toLong(), e.snr.toLong())
                    is La66Parser.Ev.TxDone -> listOf("tx_done")
                    is La66Parser.Ev.RxOn -> listOf("rx_on")
                }
            }
            assertEquals(x["line"] as String, x["events"], got)
        }
        for (key in listOf("banner_ok", "banner_bad")) {
            val b = v[key] as Map<String, Any?>
            assertEquals(b["problems"], La66Parser.checkBanner(b["banner"] as Map<String, String>, p))
        }
        // named groups become numbered groups (Android < 8 has no named groups)
        val rx = Hardware.regex("\\+EVT:RXP2P:(?P<rssi>-?\\d+):(?:x)?(?<snr>-?\\d+):[(](?P<payload>[0-9A-F]+)")
        val m = rx.regex.find("+EVT:RXP2P:-80:5:(ABCD")!!
        assertEquals("-80", rx.group(m, "rssi")); assertEquals("5", rx.group(m, "snr")); assertEquals("ABCD", rx.group(m, "payload"))
    }

    private fun tmpDir(): File = Files.createTempDirectory("textcast").toFile()

    @Test fun keyringInterop() {
        Protocol.setHardwareLimits(Hardware.load(null).maxPayload, 1)
        Protocol.applyProfile(Settings.buildProfile(mapOf("region" to "AS923-2")))
        val kr = v["keyring"] as Map<String, Any?>
        val dir = tmpDir()
        File(dir, "keys-Tuan.json").writeText(kr["tuan_json"] as String)
        File(dir, "keys-Lan.json").writeText(kr["lan_json"] as String)
        val tuan = Keyring(File(dir, "keys-Tuan.json"), "Tuan")
        assertEquals(kr["tuan_handle"], tuan.handle)
        assertEquals(kr["tuan_lid"], tuan.lid())
        assertEquals(kr["tuan_fp"], tuan.fingerprint())
        assertEquals(kr["share_news"], tuan.share("news"))       // Ed25519 signatures are deterministic
        assertEquals(kr["share_team"], tuan.share("team"))
        val frames = kr["frames"] as Map<String, String>
        val exp = kr["expected"] as Map<String, Map<String, Any?>>
        val tk = (kr["t"] as Number).toDouble()
        for (k in listOf("lan_group_sealed", "lan_group_signed", "lan_join")) {
            val o = tuan.open(Bytes.unhex(frames[k]!!), tk)
            assertNotNull(k, o)
            val e = exp[k]!!
            assertEquals(k, e["kind"], o!!.kind); assertEquals(e["source"], o.source); assertEquals(e["sender"], o.sender)
            assertEquals(e["text"], o.text); assertEquals((e["counter"] as Number).toLong(), o.counter); assertEquals(e["group"], o.group)
        }
        assertNull("replay must be refused", tuan.open(Bytes.unhex(frames["lan_group_sealed"]!!), tk))
        val lan = Keyring(File(dir, "keys-Lan.json"), "Lan")
        assertEquals(kr["lan_lid"], lan.lid())
        assertEquals(kr["lan_share_news"], lan.share("news"))
        val o = lan.open(Bytes.unhex(frames["tuan_news"]!!), tk)!!
        assertEquals(exp["tuan_news"]!!["text"], o.text)
        assertTrue(o.verified)
        // the file still loads in the other direction (Kotlin wrote it, reload it)
        tuan.save()
        assertEquals(kr["share_team"], Keyring(File(dir, "keys-Tuan.json"), "Tuan").share("team"))

        // phase 2: files for Python to check (check_kotlin.py)
        val out = System.getProperty("textcast.compat.out") ?: return
        val od = File(out).apply { deleteRecursively(); mkdirs() }
        val t2 = Keyring(File(dir, "keys-Tuan.json"), "Tuan")
        val minh = Keyring(File(od, "keys-Minh.lkey"), "Minh", "minh-passphrase")
        val minhNews = minh.newOwn("radio")
        val minhGroup = minh.newGroup("lab")
        val res = linkedMapOf<String, Any?>(
            "tuan_group_sealed" to Bytes.hex(t2.sealFor("team", "từ Android")),
            "tuan_news_signed" to Bytes.hex(t2.sealFor("news", "signed from Kotlin", signed = true)),
            "tuan_join" to Bytes.hex(t2.makeJoin("team")),
            "minh_lid" to minh.lid(), "minh_share_radio" to minhNews, "minh_share_lab" to minhGroup,
            "minh_handle" to minh.handle, "vault_blob" to Bytes.hex(Vault.create("kotlin-pass").seal(linkedMapOf("me" to "K", "n" to 5L))),
            "logcipher" to LogCipher(Bytes.unhex((v["logcipher"] as Map<String, Any?>)["secret"] as String)).encrypt("ghi log"),
        )
        File(od, "kotlin.json").writeText(Json.stringify(res, 1))
        File(od, "keys-Lan.json").writeText(kr["lan_json"] as String)
    }

    @Test fun numbers() {
        val n = v["numbers"] as Map<String, Any?>
        for (k in n["keys"] as List<Map<String, String>>) {
            assertEquals(k["number"], Numbers.numberOf(Bytes.unhex(k["pub"]!!)))
            assertEquals(k["shown"], Numbers.format(k["number"]!!))
            assertEquals(k["number"], Numbers.parse(k["shown"]))
        }
        val lk = n["lookup"] as Map<String, String>
        assertEquals(lk["frame"], Bytes.hex(Numbers.makeLookup(lk["number"]!!)))
        assertEquals(Numbers.Frame.Lookup(lk["number"]!!), Numbers.parseFrame(Bytes.unhex(lk["frame"]!!)))
        for (a in n["answers"] as List<Map<String, Any?>>) {
            val got = Numbers.makeAnswer(Bytes.unhex(a["pub"] as String), a["nick"] as String,
                (a["id"] as Number).toInt(), (a["max"] as Number).toInt()).map { Bytes.hex(it) }
            assertEquals(a["frames"], got)
        }
        for (g in n["good"] as List<String>) Numbers.parse(g)
        for (b in n["bad"] as List<String>) {
            try { Numbers.parse(b); fail("accepted $b") } catch (e: NumberException) { }
        }
    }

    @Test fun directMessages() {
        Protocol.setHardwareLimits(Hardware.load(null).maxPayload, 1)
        Protocol.applyProfile(Settings.buildProfile(mapOf("region" to "AS923-2")))
        val d = v["direct"] as Map<String, Any?>
        val dir = tmpDir()
        File(dir, "keys-Tuan.json").writeText(d["tuan_json"] as String)
        File(dir, "keys-Lan.json").writeText(d["lan_json"] as String)
        val tuan = Keyring(File(dir, "keys-Tuan.json"), "Tuan")
        val lan = Keyring(File(dir, "keys-Lan.json"), "Lan")
        val seed = Bytes.unb32(Json.parseObject(d["tuan_json"] as String)["id_seed"] as String)
        assertEquals(d["shared"], Bytes.hex(Ed25519.sharedPoint(seed, lan.pub)!!))
        for (bad in d["bad_keys"] as List<String>) assertNull(Ed25519.sharedPoint(seed, Bytes.unhex(bad)))
        val frames = d["frames"] as Map<String, String>
        val exp = d["expected"] as Map<String, Map<String, Any?>>
        val t = (d["t"] as Number).toDouble()
        for (k in listOf("text", "resend", "receipt")) {
            val o = tuan.open(Bytes.unhex(frames[k]!!), t)
            assertNotNull(k, o)
            val e = exp[k]!!
            assertEquals(k, e["kind"], o!!.kind); assertEquals(e["source"], o.source); assertEquals(e["sender"], o.sender)
            assertEquals(e["text"], o.text); assertEquals(e["inner"], o.inner)
            assertEquals((e["acks"] as List<Number>).map { it.toLong() }, o.acks)
            assertEquals((e["counter"] as Number).toLong(), o.counter)
        }
        assertNull("replay refused", tuan.open(Bytes.unhex(frames["text"]!!), t))
        val (frame, _) = tuan.sealDirect(lan.handle!!, "trả lời từ Android")
        assertEquals("trả lời từ Android", lan.open(frame)!!.text)

        val out = System.getProperty("textcast.compat.out") ?: return
        val f = File(out, "kotlin.json")
        if (!f.exists()) return
        val res = Json.parseObject(f.readText())
        File(dir, "keys-Tuan.json").writeText(d["tuan_json"] as String)
        val t3 = Keyring(File(dir, "keys-Tuan.json"), "Tuan")
        res["direct_text"] = Bytes.hex(t3.sealDirect(lan.handle!!, "từ Android").first)
        res["direct_receipt"] = Bytes.hex(t3.makeReceipt(lan.handle!!, listOf(77L, 4294967296L + 5)))
        f.writeText(Json.stringify(res, 1))
        File(out, "keys-Lan-direct.json").writeText(d["lan_json"] as String)
    }

    @Test fun vaultAndLog() {
        val x = v["vault"] as Map<String, Any?>
        val (data, vault) = Vault.open(hx(x, "blob"), x["pass"] as String)
        assertEquals(x["json"], data)
        val again = Vault.open(vault.seal(data), x["pass"] as String).first
        assertEquals(x["json"], again)
        try { Vault.open(hx(x, "blob"), "wrong-pass"); throw AssertionError("accepted a wrong passphrase") } catch (e: WrongPassphrase) { }
        val lc = v["logcipher"] as Map<String, Any?>
        val c = LogCipher(hx(lc, "secret"))
        assertEquals(lc["text"], c.decrypt(lc["enc"] as String))
        assertEquals("abc", c.decrypt(c.encrypt("abc")))
    }

    @Test fun keyringLifecycle() {
        val dir = tmpDir()
        val f = File(dir, "keys-An.lkey")
        val a = Keyring(f, "An", "an-passphrase")
        val share = a.newGroup("g1")
        a.wipe()
        assertTrue(a.locked)
        try { Keyring(f, "An", "bad-passphrase"); throw AssertionError() } catch (e: WrongPassphrase) { }
        val b = Keyring(f, "An", "an-passphrase")
        assertEquals(share, b.share("g1"))
        val exported = b.exportBlob("export-pass")
        val imported = File(dir, "keys-An2.lkey").apply { writeBytes(exported) }
        assertEquals(share, Keyring(imported, null, "export-pass").share("g1"))
    }

    @Test fun json() {
        val s = """{"a": [1, 2.5, -3, "x\"é\n", true, false, null], "b": {}, "c": []}"""
        val o = Json.parseObject(s)
        assertEquals(o, Json.parseObject(Json.stringify(o)))
        assertEquals(o, Json.parseObject(Json.stringify(o, 2)))
        assertEquals(1L, (o["a"] as List<Any?>)[0])
    }
}
