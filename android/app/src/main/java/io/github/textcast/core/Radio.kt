package io.github.textcast.core

import java.util.Locale
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.random.Random

/**
 * Radio drivers (port of la66.py and atradio.py). They talk to the module
 * through [SerialLink], which the Android app implements with USB serial and
 * the tests implement with a fake module.
 */
interface SerialLink {
    /** Read up to buf.size bytes, waiting at most timeoutMs. Returns 0 on timeout. Throws on disconnect. */
    fun read(buf: ByteArray, timeoutMs: Int): Int
    fun write(data: ByteArray)
    fun close()
}

class Packet(val payload: ByteArray, val rssi: Int? = null, val snr: Int? = null,
             val t: Double = Crypto.nowSeconds())

class TxResult(val jobId: Int, val ok: Boolean, val airtimeMs: Double, val detail: String = "")

// ---------------------------------------------------------------------------------
// LA66 line parser (pure)
// ---------------------------------------------------------------------------------

object La66Parser {
    sealed class Ev(val pos: Int) {
        class Banner(pos: Int, val key: String, val value: String) : Ev(pos)
        class RxStart(pos: Int) : Ev(pos)
        class RxData(pos: Int, val data: ByteArray) : Ev(pos)
        class Rssi(pos: Int, val v: Int) : Ev(pos)
        class Recv(pos: Int, val data: ByteArray, val rssi: Int, val snr: Int) : Ev(pos)
        class TxDone(pos: Int) : Ev(pos)
        class RxOn(pos: Int) : Ev(pos)
    }

    private const val HEXPAIRS = "(?:[0-9a-fA-F]{2}(?:\\s+|$))+"
    private val RXDONE = Regex("rxDone")
    private val DATA = Regex("Data:\\s*\\(HEX:\\)\\s*($HEXPAIRS)?")
    private val RSSI = Regex("Rssi=\\s*(-?\\d+)")
    private val RECV = Regex("((?:[0-9a-fA-F]{2}\\s+)+),\\s*(-?\\d+)\\s*,\\s*(-?\\d+)")
    private val TXDONE = Regex("txDone")
    private val RXON = Regex("RX on freq")
    private val BANNER = Regex("^(Group|Syncword|Frequency|BandWidth|Spreading Factor|Output Power|" +
        "Coding Rate|Header Type|CRC|Invert IQ|Preamble|RX Mode):\\s*(.+)$")
    private val FW = Regex("LA66 P2P Firmware\\s+(v[\\d.]+)")

    fun parse(line0: String): List<Ev> {
        val line = line0.trim()
        if (line.isEmpty() || line.startsWith("AT")) return emptyList()
        val found = ArrayList<Ev>()
        FW.find(line)?.let { found.add(Ev.Banner(it.range.first, "Firmware", it.groupValues[1])) }
        BANNER.find(line)?.let { found.add(Ev.Banner(0, it.groupValues[1], it.groupValues[2].trim())) }
        RXDONE.findAll(line).forEach { found.add(Ev.RxStart(it.range.first)) }
        val dataSpans = ArrayList<IntRange>()
        DATA.findAll(line).forEach {
            dataSpans.add(it.range)
            found.add(Ev.RxData(it.range.first, Bytes.unhex(it.groups[1]?.value ?: "")))
        }
        val recvSpans = ArrayList<IntRange>()
        RECV.findAll(line).forEach { m ->
            if (dataSpans.any { m.range.first in it }) return@forEach
            recvSpans.add(m.range)
            found.add(Ev.Recv(m.range.first, Bytes.unhex(m.groupValues[1]), m.groupValues[2].toInt(), m.groupValues[3].toInt()))
        }
        RSSI.findAll(line).forEach { m ->
            // a truncated 'Rssi= -1' glued to a RECV reply would read as '-154'
            if (recvSpans.any { m.range.first < it.first && it.first < m.range.last + 1 }) return@forEach
            val v = m.groupValues[1].toInt()
            if (v in -150..0) found.add(Ev.Rssi(m.range.first, v))
        }
        TXDONE.findAll(line).forEach { found.add(Ev.TxDone(it.range.first)) }
        RXON.findAll(line).forEach { found.add(Ev.RxOn(it.range.first)) }
        return found.sortedBy { it.pos }   // stable, like Python's sort
    }

    fun atCommands(p: AirProfile): List<String> {
        val f = String.format(Locale.ROOT, "%.3f", p.freqMhz)
        val bw = Regions.BW_CODES[p.bwHz] ?: 0
        val hdr = if (p.explicitHeader) 0 else 1
        val crc = if (p.crc) 1 else 0
        return listOf("AT+RXMOD=65535,0", "AT+FRE=$f,$f", "AT+SF=${p.sf},${p.sf}", "AT+BW=$bw,$bw",
            "AT+CR=${p.cr},${p.cr}", "AT+CRC=$crc,$crc", "AT+HEADER=$hdr,$hdr", "AT+IQ=0,0",
            "AT+PREAMBLE=${p.preamble},${p.preamble}", "AT+SYNCWORD=${if (p.privateSync) 0 else 1}",
            "AT+POWER=${p.txPowerDbm}", "AT+GROUPMOD=0,0")
    }

    fun expectedBanner(p: AirProfile): Map<String, String> = linkedMapOf(
        "Syncword" to if (p.privateSync) "0x1424" else "0x3444",
        "Spreading Factor" to "${p.sf} , ${p.sf}",
        "Coding Rate" to "4/${p.cr + 4} , 4/${p.cr + 4}",
        "Header Type" to "explicit header , explicit header",
        "CRC" to if (p.crc) "ON , ON" else "OFF , OFF",
        "Invert IQ" to "0 , 0",
        "Preamble" to "${p.preamble} , ${p.preamble}",
        "Output Power" to "${p.txPowerDbm} dBm",
        "RX Mode" to "65535 , 0",
        "Group" to "0 , 0")

    private val NUM = Regex("\\d+(?:\\.\\d+)?")

    fun checkBanner(banner: Map<String, String>, p: AirProfile): List<String> {
        val problems = ArrayList<String>()
        for ((k, want) in expectedBanner(p)) {
            val got = banner[k]
            if (got == null) problems.add("$k: not reported") else if (want !in got) problems.add("$k: got '$got', want '$want'")
        }
        for ((k, want, tol) in listOf(Triple("Frequency", p.freqMhz, 0.0005), Triple("BandWidth", p.bwHz / 1000.0, 0.05))) {
            val got = banner[k]
            val nums = NUM.findAll(got ?: "").map { it.value.toDouble() }.toList()
            if (nums.isEmpty()) problems.add("$k: not reported")
            else if (nums.take(2).any { Math.abs(it - want) > tol }) problems.add("$k: got '$got', want ${fmtG(want)}")
        }
        return problems
    }
}

// ---------------------------------------------------------------------------------
// Radio base: TX queue, rate limit, random backoff, duty cycle, reader/writer threads
// ---------------------------------------------------------------------------------

abstract class Radio(
    protected val link: SerialLink,
    val baud: Int,
    var onPacket: (Packet) -> Unit = {},
    var onTx: (TxResult) -> Unit = {},
    var onLog: (String) -> Unit = {},
) {
    val p: AirProfile get() = Protocol.profile
    val limiter = RateLimiter(Protocol.profile.ratePerS, Protocol.profile.burst)
    val meter = AirtimeMeter()
    val banner = java.util.concurrent.ConcurrentHashMap<String, String>()

    protected val wlock = Any()
    private val txq = LinkedBlockingQueue<Pair<Int, ByteArray>>()
    protected val ctrlq = LinkedBlockingQueue<String>()
    private val ids = AtomicInteger(0)
    @Volatile protected var running = false
    @Volatile var txBusy = false; protected set
    @Volatile private var paused = false
    private var reader: Thread? = null
    private var writer: Thread? = null

    // TX completion signalling
    protected val txLock = Object()
    @Volatile protected var txDoneFlag = false

    protected fun signalTxDone() = synchronized(txLock) { txDoneFlag = true; txLock.notifyAll() }

    protected fun waitTxDone(timeoutS: Double): Boolean {
        val end = System.nanoTime() + (timeoutS * 1e9).toLong()
        synchronized(txLock) {
            while (!txDoneFlag) {
                val left = (end - System.nanoTime()) / 1_000_000
                if (left <= 0) return false
                txLock.wait(left)
            }
            return true
        }
    }

    fun open() {
        running = true
        reader = Thread({ readLoop() }, "radio-reader").apply { isDaemon = true; start() }
        writer = Thread({ writeLoop() }, "radio-writer").apply { isDaemon = true; start() }
    }

    fun close() {
        running = false
        try { Thread.sleep(200) } catch (e: InterruptedException) { }
        try { link.close() } catch (e: Exception) { }
    }

    fun send(frame: ByteArray): Int {
        require(frame.size <= p.maxPayload) { "frame ${frame.size} B exceeds ${p.maxPayload} B" }
        val job = ids.incrementAndGet()
        txq.put(job to frame)
        return job
    }

    fun queued(): Int = txq.size

    fun pause(timeoutS: Double = 5.0) {
        paused = true
        val end = System.nanoTime() + (timeoutS * 1e9).toLong()
        while (txBusy && System.nanoTime() < end) Thread.sleep(50)
    }

    fun resume() { paused = false }

    /** Apply the active profile. Returns problems (empty = OK). Blocks a few seconds. */
    abstract fun configure(): List<String>

    /** Check the module's settings (or apply them where checking is impossible). */
    abstract fun resetAndCheck(): List<String>

    protected abstract fun handleLine(line: String)
    protected open fun checkDeadline() {}
    protected abstract fun transmit(jobId: Int, frame: ByteArray)

    protected open val lineEnding = "\r\n"

    fun write(cmd: String) {
        synchronized(wlock) { link.write((cmd + lineEnding).toByteArray(Charsets.US_ASCII)) }
    }

    protected fun sleepS(s: Double) { if (s > 0) Thread.sleep((s * 1000).toLong()) }

    private fun readLoop() {
        val buf = ByteArray(512)
        val line = java.io.ByteArrayOutputStream()
        while (running) {
            val n = try { link.read(buf, 200) } catch (e: Exception) {
                if (!running) break
                onLog("serial error: ${e.message}")
                sleepS(0.5)
                continue
            }
            if (n > 0) {
                for (k in 0 until n) {
                    line.write(buf[k].toInt())
                    if (buf[k] == '\n'.code.toByte()) { emit(line); }
                }
            } else if (line.size() > 0) emit(line)      // timeout with a partial line: process it anyway
            try { checkDeadline() } catch (e: Exception) { onLog("radio: ${e.message}") }
        }
    }

    private fun emit(line: java.io.ByteArrayOutputStream) {
        val s = String(line.toByteArray(), Charsets.US_ASCII)
        line.reset()
        try { handleLine(s) } catch (e: Exception) { onLog("radio: could not parse '${s.trim().take(60)}': ${e.message}") }
    }

    private fun writeLoop() {
        var job: Pair<Int, ByteArray>? = null
        var readyAt = 0.0
        while (running) {
            val ctrl = ctrlq.poll()
            if (ctrl != null) { write(ctrl); sleepS(0.05); continue }
            if (job == null) {
                job = txq.poll(50, TimeUnit.MILLISECONDS) ?: continue
                readyAt = MONOTONIC.now() + limiter.waitTime() + Random.nextDouble(0.0, maxOf(p.backoffMaxS, 1e-9))
            }
            if (MONOTONIC.now() < readyAt || paused) { sleepS(0.05); continue }
            val wait = dutyWait(job.second.size)
            if (wait > 0) { readyAt = MONOTONIC.now() + minOf(wait, 5.0); continue }
            if (!limiter.tryConsume()) { readyAt = MONOTONIC.now() + limiter.waitTime(); continue }
            try { transmit(job.first, job.second) } catch (e: Exception) {
                onLog("send failed: ${e.message}")
                onTx(TxResult(job.first, false, 0.0, e.message ?: "error"))
            }
            job = null
        }
    }

    /** Seconds until a frame fits the duty-cycle budget (0 = now). */
    fun dutyWait(frameLen: Int): Double {
        val dc = p.dutyCycle ?: return 0.0
        if (dc <= 0) return 0.0
        val budget = dc * meter.windowS * 1000
        val need = meter.totalMs() + Protocol.airtimeMs(frameLen, p) - budget
        if (need <= 0) return 0.0
        val oldest = meter.oldest() ?: return 1.0
        return maxOf(0.5, oldest + meter.windowS - meter.clock.now())
    }
}

// ---------------------------------------------------------------------------------
// Dragino LA66 P2P firmware v1.2.4 (bench-tested behaviour, see la66.py)
// ---------------------------------------------------------------------------------

class La66Radio(link: SerialLink, baud: Int = 9600, val fetchSnr: Boolean = true,
                private val settle: Double = 0.3, private val resetWait: Double = 3.0) : Radio(link, baud) {
    private class Pending(var payload: ByteArray?, var rssi: Int?, var snr: Int?, val t: Double, var deadline: Double)

    private var pending: Pending? = null
    private val rxLock = Any()
    private val rxOn = Object()
    @Volatile private var rxOnFlag = false

    override fun configure(): List<String> {
        for (cmd in La66Parser.atCommands(p)) { write(cmd); sleepS(settle) }
        return resetAndCheck()
    }

    override fun resetAndCheck(): List<String> {
        banner.clear()
        write("ATZ")
        sleepS(resetWait)
        return La66Parser.checkBanner(banner, p)
    }

    override fun handleLine(line: String) {
        for (ev in La66Parser.parse(line)) handleEvent(ev)
    }

    private fun handleEvent(ev: La66Parser.Ev) = synchronized(rxLock) {
        val now = MONOTONIC.now()
        when (ev) {
            is La66Parser.Ev.Banner -> banner[ev.key] = ev.value
            is La66Parser.Ev.RxStart -> { finalize(); pending = Pending(null, null, null, Crypto.nowSeconds(), now + 0.5) }
            is La66Parser.Ev.RxData -> {
                val pd = pending ?: Pending(null, null, null, Crypto.nowSeconds(), now).also { pending = it }
                pd.payload = ev.data
                if (fetchSnr && !txBusy) { pd.deadline = now + 1.0; ctrlq.put("AT+RECV=0") } else pd.deadline = now + 0.3
            }
            is La66Parser.Ev.Rssi -> pending?.let { if (it.rssi == null) it.rssi = ev.v }
            is La66Parser.Ev.Recv -> {
                val pd = pending
                if (pd?.payload != null) {
                    val want = pd.payload!!
                    if (ev.data.contentEquals(want) || (ev.data.size >= want.size && ev.data.copyOf(want.size).contentEquals(want))) {
                        pd.payload = ev.data; pd.rssi = ev.rssi; pd.snr = ev.snr
                        finalize()
                    } else onLog("AT+RECV returned a different packet; ignored")
                }
            }
            is La66Parser.Ev.TxDone -> signalTxDone()
            is La66Parser.Ev.RxOn -> synchronized(rxOn) { rxOnFlag = true; rxOn.notifyAll() }
        }
    }

    override fun checkDeadline() = synchronized(rxLock) {
        val pd = pending
        if (pd != null && MONOTONIC.now() >= pd.deadline) finalize()
    }

    private fun finalize() {
        val pd = pending
        pending = null
        val payload = pd?.payload ?: return
        onPacket(Packet(payload, pd.rssi, pd.snr, pd.t))
    }

    override fun transmit(jobId: Int, frame: ByteArray) {
        val cmd = "AT+SEND=0,${Bytes.hex(frame, true)},0,0"
        val ms = Protocol.airtimeMs(frame.size, p)
        val timeout = cmd.length * 10.0 / baud + ms / 1000 + 1.5
        synchronized(txLock) { txDoneFlag = false }
        synchronized(rxOn) { rxOnFlag = false }
        txBusy = true
        val ok: Boolean
        try {
            write(cmd)
            ok = waitTxDone(timeout)
            if (ok) synchronized(rxOn) { if (!rxOnFlag) rxOn.wait(1000) }
        } finally { txBusy = false }
        if (ok) meter.add(ms)
        onTx(TxResult(jobId, ok, ms, if (ok) "" else "no txDone"))
    }
}

// ---------------------------------------------------------------------------------
// Generic AT driver, driven by a HardwareProfile (RAK3172, Wio-E5, custom)
// ---------------------------------------------------------------------------------

class GenericAtRadio(link: SerialLink, val hw: HardwareProfile, baud: Int = hw.baud) : Radio(link, baud) {
    private val rx = hw.rx.map { Hardware.regex(it) }
    private val txDoneRe = Hardware.regex(hw.txDone).regex
    private val txErrRe = hw.txError.ifEmpty { null }?.let { Hardware.regex(it).regex }
    private var meta: Triple<Int?, Int?, Double>? = null
    @Volatile private var txFailed = false

    override val lineEnding: String get() = hw.lineEnding

    private fun run(cmds: List<String>) { for (c in cmds) { write(c); sleepS(hw.cmdDelay) } }

    override fun handleLine(line0: String) {
        val line = line0.trim()
        if (line.isEmpty()) return
        if (txDoneRe.containsMatchIn(line)) { signalTxDone(); return }
        if (txBusy && txErrRe?.containsMatchIn(line) == true) {
            txFailed = true; signalTxDone(); onLog("radio: $line"); return
        }
        for (re in rx) {
            val m = re.regex.find(line) ?: continue
            fun g(n: String): String? = re.group(m, n)
            var rssi = g("rssi")?.toIntOrNull()
            var snr = g("snr")?.toIntOrNull()
            val hex = g("payload")
            if (hex == null) { meta = Triple(rssi, snr, MONOTONIC.now()); return }     // meta line (e.g. Wio-E5)
            val mt = meta
            if (rssi == null && mt != null && MONOTONIC.now() - mt.third < 2.0) { rssi = mt.first; snr = mt.second }
            meta = null
            val payload = try { Bytes.unhex(hex) } catch (e: IllegalArgumentException) {
                onLog("radio: could not decode received hex: ${hex.take(40)}"); return
            }
            if (payload.isNotEmpty()) onPacket(Packet(payload, rssi, snr))
            return
        }
    }

    override fun configure(): List<String> {
        run(Hardware.renderAll(hw.init, p))
        run(Hardware.renderAll(hw.rxStart, p))
        banner.clear(); banner["Firmware"] = hw.name
        return emptyList()
    }

    override fun resetAndCheck(): List<String> = configure()

    override fun transmit(jobId: Int, frame: ByteArray) {
        val ms = Protocol.airtimeMs(frame.size, p)
        val cmd = Hardware.render(hw.send, p, frame, hw.hexUpper)
        val timeout = cmd.length * 10.0 / baud + ms / 1000 + 2.0
        synchronized(txLock) { txDoneFlag = false }
        txFailed = false
        txBusy = true
        val ok: Boolean
        try {
            run(Hardware.renderAll(hw.preSend, p))
            write(cmd)
            ok = waitTxDone(timeout) && !txFailed
        } finally {
            run(Hardware.renderAll(hw.postSend, p))
            txBusy = false
        }
        if (ok) meter.add(ms)
        onTx(TxResult(jobId, ok, ms, if (ok) "" else if (txFailed) "radio reported an error" else "no TX confirmation"))
    }
}

/** Radio for this hardware profile. */
fun makeRadio(hw: HardwareProfile, link: SerialLink, baud: Int? = null): Radio =
    if (hw.driver == "la66") La66Radio(link, baud ?: hw.baud) else GenericAtRadio(link, hw, baud ?: hw.baud)
