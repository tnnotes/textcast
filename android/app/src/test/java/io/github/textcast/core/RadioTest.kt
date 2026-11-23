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
        init { air.modules.add(this) }

        protected fun print(s: String) { for (b in (s + "\r\n").toByteArray()) out.put(b) }
        protected fun broadcast(payload: ByteArray) { for (m in air.modules) if (m !== this) m.hear(payload) }
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
}
