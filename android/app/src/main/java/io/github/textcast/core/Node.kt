package io.github.textcast.core

import java.io.File

/**
 * UI-independent node controller (port of node.py): radio + keyring + log,
 * exposed as channels and messages.
 *
 * Channels: all, public, @<handle> (direct, with delivery receipts), <group>,
 * <own stream>, <followed stream>, other.
 */
class Msg(
    val ts: Double,
    val direction: String,          // rx | tx | sys
    var kind: String,               // plain | signed | sealed | join | direct | unverified | undecoded | locked | pending | receipt
    val channel: String,
    val sender: String?,
    val text: String,
    val rssi: Int? = null,
    val snr: Int? = null,
    val size: Int = 0,
    var job: Int? = null,
    @Volatile var status: String? = null,     // tx: queued | sent | failed; direct also delivered | unconfirmed
    val airtime: Double? = null,
    val verified: Boolean = false,
    val group: Boolean = false,
    var row: Long? = null,
    val peer: String? = null,       // direct: the contact's handle
    var dmId: Long? = null,         // direct: the id a receipt refers to
    val acks: List<Long> = emptyList(),
    val resent: Boolean = false,
)

class Node(
    keysFile: File,
    nick: String?,
    passphrase: String?,
    logFile: File,
    var publicTag: Boolean = false,
    var logMaxRows: Int = 20000,
    var logMaxDays: Double = 30.0,
) {
    companion object {
        val READ_ONLY = setOf("follow", "other")
        val SENSITIVE = setOf("sealed", "signed", "join", "direct")
        @Volatile var RECEIPT_WAIT = 30.0          // s after sending: no receipt -> unconfirmed
        @Volatile var RECEIPT_DELAY = 1.0 to 4.0   // s before confirming a direct message (random)
        const val RESEND_SAME = 1800.0             // s: a resent text already shown is not shown again
        val STATUS_CODE = mapOf("failed" to 0, "sent" to 1, "delivered" to 2, "unconfirmed" to 3)
        val STATUS_NAME = STATUS_CODE.entries.associate { it.value to it.key }
        @Volatile var LOOKUP_WAIT = 10.0          // s to wait for answers to "who has this number?"
        @Volatile var LOOKUP_RETRY = 4.0          // s: ask once more if nobody answered yet
        @Volatile var ANSWER_DELAY = 0.3 to 1.5   // s before answering a lookup for my number
        @Volatile var ANSWER_GAP = 3.0            // s: answer at most this often
        val QUIET = setOf("receipt", "lookup")
    }

    /** Progress of a number lookup: status found | conflict | notfound | known | searching. */
    class LookupResult(val number: String, val status: String, val nick: String? = null,
                       val handle: String? = null, val name: String? = null)

    private val lock = Any()
    val keys = Keyring(keysFile, nick, passphrase)
    val protected = keys.encrypted
    private var cipher: LogCipher? = keys.logCipher()
    private val buffer = ArrayList<Pair<Long, Packet>>()       // private packets heard while locked
    val log = MessageLog(logFile)
    private var inserts = 0
    private val pending = java.util.concurrent.ConcurrentHashMap<Int, Msg>()
    private var meter: AirtimeMeter? = null
    private var limiter: RateLimiter? = null
    private val awaiting = java.util.concurrent.ConcurrentHashMap<String, Msg>()   // "peer|id" -> sent direct
    private val ackQueue = HashMap<String, Pair<String, MutableList<Long>>>()      // peer lower -> (handle, ids)
    private val seenDm = HashMap<String, Double>()                                 // "peer|text" -> when shown
    private val timer = java.util.Timer("textcast-receipts", true)

    var onMsg: (Msg) -> Unit = {}
    var onUpdate: (Msg) -> Unit = {}
    var onLog: (String) -> Unit = {}
    var onLookup: (LookupResult) -> Unit = {}
    private val lookups = HashMap<String, LinkedHashMap<String, String>>()     // number -> (pub hex -> nick)
    private val parts = HashMap<Int, Triple<Double, Int, HashMap<Int, ByteArray>>>()
    @Volatile private var lastAnswer = 0.0

    var radio: Radio? = null; private set
    var firmware = "?"; private set

    init {
        pruneLog()
        processPending()
    }

    val nick: String? get() = keys.me
    val handle: String? get() = keys.handle
    val locked: Boolean get() = keys.locked

    /** Name written in plain messages: nick, or handle with publicTag. */
    val callsign: String? get() = if (publicTag && handle != null) handle else nick

    // -- lifecycle ------------------------------------------------------------------

    /** Attach and start a radio; checks/applies the profile. Blocks a few seconds. Returns problems. */
    fun start(r: Radio, configure: Boolean = false, autoApply: Boolean = true): List<String> {
        // the airtime count and rate limit survive unplugging: a replug must not reset the duty cycle
        val keptMeter = meter
        val keptLimiter = limiter
        if (keptMeter != null && keptLimiter != null) { r.meter = keptMeter; r.limiter = keptLimiter }
        else { meter = r.meter; limiter = r.limiter }
        radio = r
        r.onPacket = { onPacket(it) }
        r.onTx = { onTx(it) }
        r.onLog = { onLog(it) }
        r.open()
        var problems = if (configure) emptyList() else r.resetAndCheck()
        if (configure || (problems.isNotEmpty() && autoApply && r.banner.isNotEmpty())) {
            if (problems.isNotEmpty()) onLog("module settings differ from the profile - applying them…")
            problems = r.configure()
        }
        firmware = r.banner["Firmware"] ?: "?"
        return problems
    }

    /** Switch to a new radio profile while running. Blocks ~5 s. */
    fun reconfigure(p: AirProfile): List<String> {
        val r = radio
        pruneLog()
        if (r == null) { Protocol.applyProfile(p); return emptyList() }
        r.pause()
        try {
            Protocol.applyProfile(p)
            return r.configure()
        } finally { r.resume() }
    }

    /** The module is gone: stop the radio, keep the identity open (read history, manage keys). */
    fun detachRadio() {
        val r = radio ?: return
        radio = null
        // frames still waiting fail now: sealed frames carry a time-based counter and would be
        // refused if sent much later; the user resends once the radio is back
        for ((job, m) in pending.entries.toList()) {
            pending.remove(job)
            if (m.status == "queued") { m.status = "failed"; m.row?.let { log.setTxOk(it, false) }; onUpdate(m) }
        }
        r.close()
    }

    private fun later(seconds: Double, fn: () -> Unit) {
        timer.schedule(object : java.util.TimerTask() {
            override fun run() { try { fn() } catch (e: Exception) { onLog("timer: ${e.message}") } }
        }, (seconds * 1000).toLong().coerceAtLeast(1))
    }

    fun stop() {
        timer.cancel()
        radio?.close()
        radio = null
        keys.close()
    }

    fun pruneLog(): Int = log.prune(logMaxRows, logMaxDays)

    private fun logged() { if (++inserts % 200 == 0) pruneLog() }

    // -- channels ----------------------------------------------------------------------

    fun channels(): List<Pair<String, String>> = synchronized(lock) {
        if (keys.locked) return listOf("all" to "all", "public" to "public", "other" to "other")
        listOf("all" to "all", "public" to "public") + keys.directContacts().map { "@" + it.first to "direct" } +
            keys.groups().map { it to "group" } +
            keys.own().map { it to "own" } + keys.followed().map { it to "follow" } + listOf("other" to "other")
    }

    fun channelType(channel: String): String = channels().firstOrNull { it.first == channel }?.second ?: "other"
    fun canSend(channel: String): Boolean = channelType(channel) !in READ_ONLY

    /** How to show a channel: the contact's name for "@handle", else the label. */
    fun channelTitle(channel: String): String {
        if (!channel.startsWith("@")) return channel
        if (!locked) synchronized(lock) { keys.directContacts().firstOrNull { "@" + it.first == channel }?.let { return it.second } }
        return channel.substring(1)
    }

    fun byteCount(channel: String, text: String): Int {
        if (text.isBlank()) return 0
        val t = channelType(channel)
        return if (t in setOf("group", "own", "direct"))
            Bytes.utf8(Protocol.normalize(text.trim())).size + Crypto.FRAME_OVERHEAD + (if (t == "direct") 1 else 0)
        else Protocol.frameLenPlain(Protocol.compose(text, callsign))
    }

    val maxBytes: Int get() = Protocol.profile.maxPayload

    // -- sending ---------------------------------------------------------------------------

    fun send(channel: String, text0: String, signed: Boolean = false): Msg {
        val r = radio ?: throw KeyException("plug in the radio to send")
        val ctype = channelType(channel)
        val text = text0.trim()
        if (ctype in READ_ONLY) throw KeyException("'$channel' is read-only - switch to public, a group or your own stream")
        if (locked && ctype in setOf("group", "own", "direct")) throw LockedException("identity is locked - unlock first")
        if (ctype == "direct") return sendDirect(r, channel, text)
        val frame: ByteArray; val kind: String
        synchronized(lock) {
            if (ctype == "group" || ctype == "own") {
                frame = keys.sealFor(channel, text, signed); kind = if (signed) "signed" else "sealed"
            } else {
                frame = Protocol.encodePlain(Protocol.compose(text, callsign)); kind = "plain"
            }
        }
        val job = r.send(frame)
        val m = Msg(Crypto.nowSeconds(), "tx", kind, if (ctype == "all") "public" else channel,
            handle ?: nick ?: "me", text, size = frame.size, job = job, status = "queued",
            airtime = Protocol.airtimeMs(frame.size), verified = true, group = ctype == "group")
        val (ch, snd) = logMeta(m)
        m.row = log.add("tx", frame, kind, logText(m), airtimeMs = m.airtime, channel = ch, sender = snd)
        logged()
        pending[job] = m
        return m
    }

    private fun sendDirect(r: Radio, channel: String, text: String): Msg {
        val peer = channel.substring(1)
        val (frame, i) = synchronized(lock) { keys.sealDirect(peer, text) }
        val job = r.send(frame)
        val m = Msg(Crypto.nowSeconds(), "tx", "direct", channel, handle ?: nick ?: "me", text, size = frame.size,
            job = job, status = "queued", airtime = Protocol.airtimeMs(frame.size), verified = true,
            peer = peer, dmId = Dm.id(i))
        val (ch, snd) = logMeta(m)
        m.row = log.add("tx", frame, "direct", logText(m), airtimeMs = m.airtime, channel = ch, sender = snd)
        logged()
        pending[job] = m
        return m
    }

    /** Send a direct message again (no receipt came). The receiver doesn't show it twice. */
    fun resend(m: Msg): Msg {
        val r = radio ?: throw KeyException("plug in the radio to send")
        if (m.kind != "direct" || m.direction != "tx" || m.peer == null) throw KeyException("only direct messages can be resent")
        if (m.status == "queued" || m.status == "delivered") return m
        val (frame, i) = synchronized(lock) { keys.sealDirect(m.peer, m.text, resend = true) }
        m.dmId = Dm.id(i); m.status = "queued"
        val job = r.send(frame)
        m.job = job
        pending[job] = m
        onUpdate(m)
        return m
    }

    private fun setStatus(m: Msg, status: String) {
        m.status = status
        m.row?.let { log.setStatus(it, STATUS_CODE.getValue(status)) }
        onUpdate(m)
    }

    private fun confirm(peer: String, id: Long) {
        val first: Boolean
        synchronized(ackQueue) {
            val e = ackQueue.getOrPut(peer.lowercase()) { peer to ArrayList() }
            if (id !in e.second) e.second.add(id)
            first = e.second.size == 1
        }
        if (first) {
            val (lo, hi) = RECEIPT_DELAY
            later(lo + Math.random() * (hi - lo)) { sendReceipts(peer.lowercase()) }
        }
    }

    private fun sendReceipts(key: String) {
        val e = synchronized(ackQueue) { ackQueue.remove(key) } ?: return
        val r = radio ?: return
        if (locked || e.second.isEmpty()) return
        for (chunk in e.second.chunked(Dm.MAX_ACKS)) r.send(synchronized(lock) { keys.makeReceipt(e.first, chunk) })
    }

    private fun onReceipt(m: Msg) {
        for (id in m.acks) {
            val sent = awaiting.remove("${m.peer!!.lowercase()}|$id") ?: continue
            if (sent.status != "delivered") setStatus(sent, "delivered")
        }
    }

    fun announce(group: String): Msg {
        val r = radio ?: throw KeyException("plug in the radio to send")
        val frame = synchronized(lock) { keys.makeJoin(group) }
        val job = r.send(frame)
        val m = Msg(Crypto.nowSeconds(), "tx", "join", group, handle, "announcing myself", size = frame.size,
            job = job, status = "queued", airtime = Protocol.airtimeMs(frame.size), group = true)
        pending[job] = m
        return m
    }

    private fun onTx(r: TxResult) {
        val m = pending.remove(r.jobId) ?: return
        if (m.kind == "direct" && r.ok) {
            val id = m.dmId
            awaiting["${m.peer!!.lowercase()}|$id"] = m
            later(RECEIPT_WAIT) { if (m.status == "sent" && m.dmId == id) setStatus(m, "unconfirmed") }
        }
        if (m.status == "delivered") return            // a receipt for an earlier copy came first
        m.status = if (r.ok) "sent" else "failed"
        m.row?.let { log.setTxOk(it, r.ok) }
        onUpdate(m)
    }

    // -- receiving -------------------------------------------------------------------------

    // -- TextCast numbers ----------------------------------------------------------------

    val number: String get() = keys.number

    /** Ask nearby devices "who has this number?". The result arrives through onLookup. */
    fun lookup(text: String): LookupResult {
        val number = Numbers.parse(text)
        if (number == keys.number) throw NumberException("that is your own number")
        if (!locked) keys.contactByNumber(number)?.let {
            return LookupResult(number, "known", name = it.name, handle = it.handle)
        }
        val r = radio ?: throw KeyException("plug in the radio to send")
        synchronized(lookups) { lookups[number] = LinkedHashMap() }
        r.send(Numbers.makeLookup(number))
        later(LOOKUP_RETRY) {
            val none = synchronized(lookups) { lookups[number]?.isEmpty() == true }
            if (none) radio?.send(Numbers.makeLookup(number))
        }
        later(LOOKUP_WAIT) {
            val none = synchronized(lookups) { if (lookups[number]?.isEmpty() == true) { lookups.remove(number); true } else false }
            if (none) onLookup(LookupResult(number, "notfound"))
        }
        return LookupResult(number, "searching")
    }

    /** Save the person found by lookup(); returns their chat channel "@handle". */
    fun addFound(number: String, name: String? = null): String {
        val found = synchronized(lookups) { lookups[number]?.toList() }
        if (found == null || found.size != 1) throw KeyException("nobody (or more than one key) answered for that number")
        val (pubHex, nick) = found[0]
        val pub = Bytes.unhex(pubHex)
        synchronized(lock) { keys.addFound(pub, nick, number, name) }
        synchronized(lookups) { lookups.remove(number) }
        return "@" + Ids.makeHandle(nick, pub)
    }

    fun setFindable(on: Boolean) = synchronized(lock) { keys.setFindable(on) }

    private fun onDirectory(pkt: Packet) {
        val now = Crypto.nowSeconds()
        when (val d = Numbers.parseFrame(pkt.payload)) {
            null -> return
            is Numbers.Frame.Lookup -> {
                val nk = nick
                if (d.number == keys.number && keys.findable && nk != null && radio != null && now - lastAnswer > ANSWER_GAP) {
                    lastAnswer = now
                    val (lo, hi) = ANSWER_DELAY
                    later(lo + Math.random() * (hi - lo)) {
                        val r = radio ?: return@later
                        for (f in Numbers.makeAnswer(keys.pub, nk, (Math.random() * 256).toInt(), Protocol.profile.maxPayload)) r.send(f)
                    }
                }
            }
            is Numbers.Frame.Answer -> {
                val data: ByteArray
                synchronized(parts) {
                    parts.entries.removeIf { now - it.value.first > 30 }
                    var st = parts[d.id]
                    if (st == null || st.second != d.parts) { st = Triple(now, d.parts, HashMap()); parts[d.id] = st }
                    st.third[d.part] = d.chunk
                    if (st.third.size < d.parts) return
                    parts.remove(d.id)
                    data = Bytes.concat(*Array(d.parts) { st.third[it]!! })
                }
                if (data.size < 33) return
                val pub = data.copyOfRange(0, 32)
                val nk = Bytes.utf8Lenient(data.copyOfRange(32, data.size))
                if (synchronized(lookups) { lookups.isEmpty() }) return
                val number = Numbers.numberOf(pub)
                val result = synchronized(lookups) {
                    val look = lookups[number] ?: return
                    if (look.containsKey(Bytes.hex(pub))) return
                    look[Bytes.hex(pub)] = nk
                    if (look.size == 1) LookupResult(number, "found", nk, Ids.makeHandle(nk, pub))
                    else LookupResult(number, "conflict", look.values.joinToString(", "))
                }
                onLookup(result)
            }
        }
    }

    private fun onPacket(pkt: Packet) {
        if (Numbers.isDirectory(pkt.payload)) {
            log.add("rx", pkt.payload, "lookup", null, pkt.rssi, pkt.snr, ts = pkt.t)
            logged()
            onDirectory(pkt)
            return
        }
        if (locked && Protocol.decode(pkt.payload).kind == "reserved") {
            val row = log.add("rx", pkt.payload, "pending", null, pkt.rssi, pkt.snr, ts = pkt.t)
            logged()
            synchronized(lock) { buffer.add(row to pkt) }
            onMsg(Msg(pkt.t, "rx", "pending", "other", null, "[${pkt.payload.size} B private - unlock to read]",
                pkt.rssi, pkt.snr, pkt.payload.size))
            return
        }
        val m = classify(pkt)
        if (m.kind == "receipt") {
            log.add("rx", pkt.payload, "receipt", null, pkt.rssi, pkt.snr, ts = pkt.t)
            logged()
            onReceipt(m)
            return
        }
        if (m.kind == "direct") {
            confirm(m.peer!!, m.dmId!!)
            val key = "${m.peer.lowercase()}|${m.text}"
            val now = Crypto.nowSeconds()
            synchronized(seenDm) {
                if (m.resent && now - (seenDm[key] ?: 0.0) < RESEND_SAME) return   // shown already: just confirm
                seenDm[key] = now
            }
        }
        val (ch, snd) = logMeta(m)
        m.row = log.add("rx", pkt.payload, m.kind, logText(m), pkt.rssi, pkt.snr, ts = pkt.t, channel = ch, sender = snd)
        logged()
        onMsg(m)
    }

    private fun protect(m: Msg, value: String?): String? {
        val c = cipher
        return if (value != null && protected && m.kind in SENSITIVE && c != null) c.encrypt(value) else value
    }

    private fun logText(m: Msg) = protect(m, m.text)

    /** (channel, sender) for the log, encrypted for private messages. */
    private fun logMeta(m: Msg): Pair<String?, String?> {
        val flag = if (m.group) "g" else if (m.verified) "v" else "u"
        val sender = m.sender?.let { "$flag:$it" }
        return protect(m, m.channel) to protect(m, sender)
    }

    fun classify(pkt: Packet): Msg {
        val d = Protocol.decode(pkt.payload)
        fun mk(kind: String, channel: String, sender: String?, text: String, verified: Boolean = false, group: Boolean = false) =
            Msg(pkt.t, "rx", kind, channel, sender, text, pkt.rssi, pkt.snr, pkt.payload.size, verified = verified, group = group)
        if (d.kind == "plain") {
            val cs = d.callsign
            var text = d.text ?: ""
            if (cs != null) text = text.substring(cs.length + 2)
            return mk("plain", "public", cs, text)
        }
        if (d.kind == "reserved") {
            val op = synchronized(lock) { keys.open(pkt.payload) }
            if (op != null && op.kind == "direct") {
                val peer = op.source.substring(1)
                if (op.inner == "receipt")
                    return Msg(pkt.t, "rx", "receipt", op.source, op.sender, "", pkt.rssi, pkt.snr, pkt.payload.size,
                        verified = true, peer = peer, acks = op.acks)
                return Msg(pkt.t, "rx", "direct", op.source, op.sender, Protocol.clean(op.text), pkt.rssi, pkt.snr,
                    pkt.payload.size, verified = true, peer = peer, dmId = Dm.id(op.counter), resent = op.inner == "resend")
            }
            if (op != null) {
                if (op.group) return mk(op.kind, op.source, op.sender, Protocol.clean(op.text), group = true)
                val name = synchronized(lock) { if (op.verified) keys.display(op.sender, op.pub) else op.sender }
                return mk(op.kind, op.source, name, Protocol.clean(op.text), verified = op.verified)
            }
            if (d.text != null) return mk("unverified", "public", null, d.text)
            return mk("locked", "other", null, "[${pkt.payload.size} B sealed]")
        }
        return mk("undecoded", "other", null, "[${pkt.payload.size} B undecoded]")
    }

    // -- lock / unlock -----------------------------------------------------------------------

    fun lockIdentity() {
        if (!protected) throw KeyException("this identity has no passphrase")
        synchronized(lock) { keys.wipe(); cipher = null }
    }

    /** Throws WrongPassphrase. Opens private packets heard while locked. */
    fun unlock(passphrase: String) {
        synchronized(lock) {
            keys.unlock(passphrase)
            cipher = keys.logCipher()
            val buffered = ArrayList(buffer); buffer.clear()
            for ((row, pkt) in buffered) {
                val m = classify(pkt)
                val (ch, snd) = logMeta(m)
                log.update(row, m.kind, logText(m), ch, snd)
            }
        }
    }

    /** Open private packets logged while locked in an earlier session. */
    fun processPending() {
        if (locked) return
        for (r in log.rows(500, kind = "pending")) {
            val m = classify(Packet(r.payload, r.rssi, r.snr, r.ts))
            val (ch, snd) = logMeta(m)
            log.update(r.id, m.kind, logText(m), ch, snd)
        }
    }

    fun history(n: Int = 300): List<Msg> {
        val out = ArrayList<Msg>()
        val c = cipher
        for (r in log.rows(n)) {
            if (r.kind in QUIET) continue
            var text = r.text; var channel = r.channel; var sender = r.sender
            if (listOf(text, channel, sender).any { it != null && it.startsWith(LogCipher.PREFIX) }) {
                if (c == null) continue
                text = c.decrypt(text); channel = c.decrypt(channel); sender = c.decrypt(sender)
            }
            if (r.direction == "rx" && r.kind == "pending") text = "[${r.payload.size} B private - unlock to read]"
            if (text == null) text = Protocol.decode(r.payload).display()
            var flag = "u"; var name = sender
            if (sender != null && sender.length > 1 && sender[1] == ':' && sender[0] in "gvu") {
                flag = sender.substring(0, 1); name = sender.substring(2)
            }
            val status = if (r.direction == "tx") STATUS_NAME[r.txOk] ?: "failed" else null
            out.add(Msg(r.ts, r.direction, r.kind ?: "plain",
                channel ?: (if (r.kind == "plain") "public" else "other"), name, text, r.rssi, r.snr, r.payload.size,
                status = status, airtime = r.airtimeMs, verified = flag == "v", group = flag == "g", row = r.id,
                peer = channel?.takeIf { it.startsWith("@") }?.substring(1)))
        }
        return out
    }

    // -- keys ---------------------------------------------------------------------------------

    fun keyNewStream(name: String): String = synchronized(lock) { keys.newOwn(name) }
    fun keyNewGroup(label: String): String = synchronized(lock) { keys.newGroup(label) }

    /** Follow a stream, join a group, or save a contact. Returns (label, type). */
    fun keyAdd(share0: String, name: String? = null): Pair<String, String> {
        val share = share0.trim()
        val kind = share.substringBefore(':')
        val label: String
        synchronized(lock) {
            when (kind) {
                "lts1", "lts2", "lts3" -> return keys.follow(share, name) to "follow"
                "lid1" -> return keys.addContact(share, name) to "contact"
                "ltg1", "ltg2" -> label = keys.joinGroup(share, name)
                else -> throw KeyException("paste a string starting with lts3:, ltg2: or lid1:")
            }
        }
        if (radio != null) {
            try { announce(label) } catch (e: KeyException) { onLog("joined '$label', but ${e.message}") }
        }
        return label to "group"
    }

    fun keyShare(label: String): String = synchronized(lock) { keys.share(label) }
    fun keyDelete(label: String): Boolean = synchronized(lock) { keys.remove(label) }
    fun keyMember(group: String, name: String) = synchronized(lock) { keys.addMember(group, name) }
    fun contactDel(name: String): Boolean = synchronized(lock) { keys.removeContact(name) }

    // -- status ------------------------------------------------------------------------------

    class Stats(val dutyPct: Double, val queue: Int, val waitS: Double, val airtimeS: Double, val dutyLimitPct: Double)

    fun stats(): Stats {
        val r = radio ?: return Stats(0.0, 0, 0.0, 0.0, (Protocol.profile.dutyCycle ?: 0.0) * 100)
        val wait = maxOf(r.limiter.waitTime(), if (r.queued() > 0) r.dutyWait(Protocol.profile.maxPayload) else 0.0)
        return Stats(r.meter.dutyCycle() * 100, r.queued(), wait, r.meter.totalMs() / 1000,
            (Protocol.profile.dutyCycle ?: 0.0) * 100)
    }
}
