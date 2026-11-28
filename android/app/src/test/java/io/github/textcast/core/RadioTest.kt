package io.github.textcast.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** Drivers and Node against simulated modules that share one "air". */
class RadioTest {
    class Air { val modules = CopyOnWriteArrayList<FakeModule>() }

    /** A fake serial module: parses AT commands written to it, prints replies. */
    abstract class FakeModule(val air: Air) : SerialLink {
        private val out = LinkedBlockingQueue<Byte>()
        private val line = StringBuilder()
        val written = CopyOnWriteArrayList<String>()
        @Volatile var muted = false                 // what it sends is lost (to test missing receipts)
        init { air.modules.add(this) }

        protected fun print(s: String) { for (b in (s + "\r\n").toByteArray()) out.put(b) }
        protected fun broadcast(payload: ByteArray) { if (!muted) for (m in air.modules) if (m !== this) m.hear(payload) }
        abstract fun hear(payload: ByteArray)
        abstract fun command(cmd: String)

        override fun read(buf: ByteArray, timeoutMs: Int): Int {
            val first = out.poll(timeoutMs.toLong(), TimeUnit.MILLISECONDS) ?: return 0
            buf[0] = first
            var n = 1
            while (n < buf.size) { val b = out.poll() ?: break; buf[n++] = b }
            return n
        }

        override fun write(data: ByteArray) {
            for (c in String(data)) {
                if (c == '\n') { val cmd = line.toString().trim(); line.setLength(0); if (cmd.isNotEmpty()) { written.add(cmd); command(cmd) } }
                else line.append(c)
            }
        }

        override fun close() { air.modules.remove(this) }
    }

    class FakeLa66(air: Air) : FakeModule(air) {
        private var last: ByteArray? = null
        override fun hear(payload: ByteArray) {
            last = payload
            print("rxDone")
            print("Data: (HEX:) " + payload.joinToString(" ") { String.format("%02X", it) } + " ")
            print("Rssi= -80")
        }
        override fun command(cmd: String) {
            val p = Protocol.profile
            when {
                cmd.startsWith("AT+SEND=0,") -> {
                    val hex = cmd.removePrefix("AT+SEND=0,").substringBefore(',')
                    broadcast(Bytes.unhex(hex)); print("txDone"); print("RX on freq ${(p.freqMhz * 1e6).toLong()}")
                }
                cmd == "AT+RECV=0" -> last?.let { print(it.joinToString(" ") { b -> String.format("%02X", b) } + " ,-80,7") }
                cmd == "ATZ" -> {
                    print("LA66 P2P Firmware v1.2.4")
                    print("Group: 0 , 0"); print("Syncword: 0x1424")
                    print(String.format(java.util.Locale.ROOT, "Frequency: %.3f , %.3f", p.freqMhz, p.freqMhz))
                    print("BandWidth: ${fmtG(p.bwHz / 1000.0)} kHz , ${fmtG(p.bwHz / 1000.0)} kHz")
                    print("Spreading Factor: ${p.sf} , ${p.sf}"); print("Output Power: ${p.txPowerDbm} dBm")
                    print("Coding Rate: 4/${p.cr + 4} , 4/${p.cr + 4}"); print("Header Type: explicit header , explicit header")
                    print("CRC: ON , ON"); print("Invert IQ: 0 , 0"); print("Preamble: ${p.preamble} , ${p.preamble}")
                    print("RX Mode: 65535 , 0")
                }
            }
        }
    }

    class FakeRak(air: Air) : FakeModule(air) {
        private var receiving = false
        override fun hear(payload: ByteArray) { if (receiving) print("+EVT:RXP2P:-70:6:" + Bytes.hex(payload, true)) }
        override fun command(cmd: String) {
            when {
                cmd == "AT+PRECV=0" -> { receiving = false; print("OK") }
                cmd == "AT+PRECV=65534" -> { receiving = true; print("OK") }
                cmd.startsWith("AT+PSEND=") -> {
                    if (receiving) { print("AT_BUSY_ERROR"); return }
                    print("OK"); broadcast(Bytes.unhex(cmd.removePrefix("AT+PSEND="))); print("+EVT:TXP2P DONE")
                }
                else -> print("OK")
            }
        }
    }

    @Before fun fastProfile() {
        val hw = Hardware.load(null)
        Protocol.setHardwareLimits(hw.maxPayload, hw.firmwareOverhead)
        val p = Settings.buildProfile(mapOf("region" to "AS923-2"))
        p.backoffMaxS = 0.01; p.ratePerS = 100.0; p.burst = 100
        Protocol.applyProfile(p)
    }

    private fun tmp(): File = Files.createTempDirectory("tc").toFile()

    @Test fun la66AndRakTalk() {
        val air = Air()
        val la = La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3)
        val rak = GenericAtRadio(FakeRak(air), Hardware.load("rak3172-rui3").copy(cmdDelay = 0.0))
        val gotLa = LinkedBlockingQueue<Packet>(); val gotRak = LinkedBlockingQueue<Packet>()
        val txs = LinkedBlockingQueue<TxResult>()
        la.onPacket = { gotLa.put(it) }; rak.onPacket = { gotRak.put(it) }
        la.onTx = { txs.put(it) }; rak.onTx = { txs.put(it) }
        la.open(); rak.open()
        assertEquals(emptyList<String>(), la.configure())
        assertEquals(emptyList<String>(), rak.configure())
        la.send(Bytes.unhex("00414243"))
        val p1 = gotRak.poll(5, TimeUnit.SECONDS)!!
        assertEquals("00414243", Bytes.hex(p1.payload)); assertEquals(-70, p1.rssi); assertEquals(6, p1.snr)
        assertTrue(txs.poll(5, TimeUnit.SECONDS)!!.ok)
        rak.send(Bytes.unhex("00ff10"))
        val p2 = gotLa.poll(5, TimeUnit.SECONDS)!!
        assertEquals("00ff10", Bytes.hex(p2.payload)); assertEquals(-80, p2.rssi); assertEquals(7, p2.snr)
        assertTrue(txs.poll(5, TimeUnit.SECONDS)!!.ok)
        la.close(); rak.close()
    }

    @Test fun nodesChat() {
        val air = Air()
        val dir = tmp()
        val tuan = Node(File(dir, "keys-Tuan.lkey"), "Tuan", "tuan-passphrase", File(dir, "log-Tuan.jsonl"))
        val lan = Node(File(dir, "keys-Lan.json"), "Lan", null, File(dir, "log-Lan.jsonl"))
        val tq = LinkedBlockingQueue<Msg>(); val lq = LinkedBlockingQueue<Msg>()
        tuan.onMsg = { tq.put(it) }; lan.onMsg = { lq.put(it) }
        assertEquals(emptyList<String>(), tuan.start(La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3)))
        assertEquals(emptyList<String>(), lan.start(GenericAtRadio(FakeRak(air), Hardware.load("rak3172-rui3").copy(cmdDelay = 0.0))))

        tuan.send("public", "xin chào")
        val m1 = lq.poll(5, TimeUnit.SECONDS)!!
        assertEquals("plain", m1.kind); assertEquals("Tuan", m1.sender); assertEquals("xin chào", m1.text)

        val share = tuan.keyNewGroup("team")
        assertEquals("team" to "group", lan.keyAdd(share))          // also announces Lan to the group
        val join = tq.poll(5, TimeUnit.SECONDS)!!
        assertEquals("join", join.kind); assertEquals("team", join.channel)

        lan.send("team", "bí mật")
        val m2 = tq.poll(5, TimeUnit.SECONDS)!!
        assertEquals("sealed", m2.kind); assertEquals("team", m2.channel); assertEquals("bí mật", m2.text); assertTrue(m2.group)

        // locked: private packets wait, then open on unlock
        tuan.lockIdentity()
        lan.send("team", "while locked")
        assertEquals("pending", tq.poll(5, TimeUnit.SECONDS)!!.kind)
        tuan.unlock("tuan-passphrase")
        val h = tuan.history()
        assertTrue(h.any { it.text == "while locked" && it.channel == "team" })
        assertTrue(h.any { it.text == "bí mật" })
        tuan.stop(); lan.stop()

        // the log is encrypted for private messages and readable after a restart
        val raw = File(dir, "log-Tuan.jsonl").readText()
        assertTrue("private text must not be in the log", "bí mật" !in raw)
        val again = Node(File(dir, "keys-Tuan.lkey"), "Tuan", "tuan-passphrase", File(dir, "log-Tuan.jsonl"))
        assertTrue(again.history().any { it.text == "bí mật" && it.group })
        again.stop()
    }

    @Test fun directMessagesAndReceipts() {
        val air = Air()
        val dir = tmp()
        Node.RECEIPT_DELAY = 0.05 to 0.1; Node.RECEIPT_WAIT = 1.0
        val tuan = Node(File(dir, "keys-Tuan.json"), "Tuan", null, File(dir, "log-Tuan.jsonl"))
        val lan = Node(File(dir, "keys-Lan.json"), "Lan", null, File(dir, "log-Lan.jsonl"))
        val lq = LinkedBlockingQueue<Msg>(); val tq = LinkedBlockingQueue<Msg>(); val tu = LinkedBlockingQueue<String>()
        lan.onMsg = { lq.put(it) }; tuan.onMsg = { tq.put(it) }; tuan.onUpdate = { tu.put(it.status ?: "") }
        val lanModule = FakeLa66(air)
        tuan.start(La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3))
        lan.start(La66Radio(lanModule, settle = 0.0, resetWait = 0.3))
        assertEquals("Lan" to "contact", tuan.keyAdd(lan.keys.lid()))
        lan.keyAdd(tuan.keys.lid())
        val ch = "@" + lan.handle
        assertTrue(tuan.channels().contains(ch to "direct"))
        assertEquals("Lan", tuan.channelTitle(ch))
        assertEquals(2 + 8, tuan.byteCount(ch, "hi"))

        val m = tuan.send(ch, "gặp lúc 7 giờ")
        val got = lq.poll(5, TimeUnit.SECONDS)!!
        assertEquals("direct", got.kind); assertEquals("Tuan", got.sender); assertEquals("gặp lúc 7 giờ", got.text)
        assertEquals("@" + tuan.handle, got.channel)
        assertEquals("sent", tu.poll(5, TimeUnit.SECONDS))
        assertEquals("delivered", tu.poll(5, TimeUnit.SECONDS))   // Lan's app confirmed by itself
        assertEquals("delivered", m.status)
        assertEquals(null, tq.poll(300, TimeUnit.MILLISECONDS))       // receipts are not messages
        assertTrue(tuan.history().any { it.kind == "direct" && it.status == "delivered" })

        lanModule.muted = true                                          // Lan's receipt gets lost
        val m2 = tuan.send(ch, "còn đó không?")
        assertEquals("còn đó không?", lq.poll(5, TimeUnit.SECONDS)!!.text)
        assertEquals("sent", tu.poll(5, TimeUnit.SECONDS))
        assertEquals("unconfirmed", tu.poll(5, TimeUnit.SECONDS))
        lanModule.muted = false
        tuan.resend(m2)
        assertEquals("queued", tu.poll(5, TimeUnit.SECONDS))
        assertEquals("sent", tu.poll(5, TimeUnit.SECONDS))
        assertEquals("delivered", tu.poll(5, TimeUnit.SECONDS))
        assertEquals(null, lq.poll(300, TimeUnit.MILLISECONDS))       // not shown twice
        tuan.stop(); lan.stop()
        Node.RECEIPT_DELAY = 1.0 to 4.0; Node.RECEIPT_WAIT = 30.0
    }

    @Test fun findByNumber() {
        val air = Air()
        val dir = tmp()
        Node.ANSWER_DELAY = 0.05 to 0.1; Node.LOOKUP_RETRY = 2.0; Node.LOOKUP_WAIT = 4.0; Node.ANSWER_GAP = 0.0
        val tuan = Node(File(dir, "keys-Tuan.json"), "Tuan", null, File(dir, "log-Tuan.jsonl"))
        val lan = Node(File(dir, "keys-Lan.json"), "Lan", null, File(dir, "log-Lan.jsonl"))
        val found = LinkedBlockingQueue<Node.LookupResult>()
        val lq = LinkedBlockingQueue<Msg>()
        tuan.onLookup = { found.put(it) }; lan.onMsg = { lq.put(it) }
        tuan.start(La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3))
        lan.start(La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3))
        assertEquals("searching", tuan.lookup(Numbers.format(lan.number)).status)
        val r = found.poll(8, TimeUnit.SECONDS)!!
        assertEquals("found", r.status); assertEquals("Lan", r.nick)
        val ch = tuan.addFound(lan.number)
        assertEquals("@" + lan.handle, ch)
        assertTrue(!tuan.keys.contactInfo()[0].verified)
        assertEquals("known", tuan.lookup(lan.number).status)
        assertEquals(null, lq.poll(300, TimeUnit.MILLISECONDS))         // lookups are not messages
        lan.setFindable(false)
        val c = Node(File(dir, "keys-Minh.json"), "Minh", null, File(dir, "log-Minh.jsonl"))
        c.setFindable(false)
        c.start(La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3))
        tuan.lookup(c.number)
        assertEquals("notfound", found.poll(8, TimeUnit.SECONDS)!!.status)
        tuan.stop(); lan.stop(); c.stop()
        Node.ANSWER_DELAY = 0.3 to 1.5; Node.LOOKUP_RETRY = 4.0; Node.LOOKUP_WAIT = 10.0; Node.ANSWER_GAP = 3.0
    }

    @Test fun unplugAndPlugBack() {
        val air = Air()
        val dir = tmp()
        val n = Node(File(dir, "keys-Tuan.json"), "Tuan", null, File(dir, "log-Tuan.jsonl"))
        val r1 = La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3)
        n.start(r1)
        for (k in 0 until 6) n.send("public", "hello $k")          // more than the burst: some wait
        Thread.sleep(500)
        val spent = r1.meter.totalMs()
        assertTrue(spent > 0)
        val waiting = n.history().filter { it.direction == "tx" }.size
        n.detachRadio()                                             // unplugged
        try { n.send("public", "now"); throw AssertionError("sent without a radio") } catch (e: KeyException) { }
        val r2 = La66Radio(FakeLa66(air), settle = 0.0, resetWait = 0.3)
        n.start(r2)                                                 // plugged back in
        assertEquals(spent, r2.meter.totalMs(), 0.001)
        assertTrue(waiting >= 6)
        n.stop()
    }
}
