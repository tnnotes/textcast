package io.github.textcast.core

import java.io.File

/**
 * UI-independent node controller (port of node.py): radio + keyring + log,
 * exposed as channels and messages.
 *
 * Channels: all, public, <group>, <own stream>, <followed stream>, other.
 */
class Msg(
    val ts: Double,
    val direction: String,          // rx | tx | sys
    var kind: String,               // plain | signed | sealed | join | unverified | undecoded | locked | pending
    val channel: String,
    val sender: String?,
    val text: String,
    val rssi: Int? = null,
    val snr: Int? = null,
    val size: Int = 0,
    val job: Int? = null,
    @Volatile var status: String? = null,     // tx: queued | sent | failed
    val airtime: Double? = null,
    val verified: Boolean = false,
    val group: Boolean = false,
    var row: Long? = null,
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
        val SENSITIVE = setOf("sealed", "signed", "join")
    }

    private val lock = Any()
    val keys = Keyring(keysFile, nick, passphrase)
    val protected = keys.encrypted
    private var cipher: LogCipher? = keys.logCipher()
    private val buffer = ArrayList<Pair<Long, Packet>>()       // private packets heard while locked
    val log = MessageLog(logFile)
    private var inserts = 0
    private val pending = java.util.concurrent.ConcurrentHashMap<Int, Msg>()

    var onMsg: (Msg) -> Unit = {}
    var onUpdate: (Msg) -> Unit = {}
    var onLog: (String) -> Unit = {}

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
        r.close()
    }

    fun stop() {
        radio?.close()
        radio = null
        keys.close()
    }

    fun pruneLog(): Int = log.prune(logMaxRows, logMaxDays)

    private fun logged() { if (++inserts % 200 == 0) pruneLog() }

    // -- channels ----------------------------------------------------------------------

    fun channels(): List<Pair<String, String>> = synchronized(lock) {
        if (keys.locked) return listOf("all" to "all", "public" to "public", "other" to "other")
        listOf("all" to "all", "public" to "public") + keys.groups().map { it to "group" } +
            keys.own().map { it to "own" } + keys.followed().map { it to "follow" } + listOf("other" to "other")
    }

    fun channelType(channel: String): String = channels().firstOrNull { it.first == channel }?.second ?: "other"
    fun canSend(channel: String): Boolean = channelType(channel) !in READ_ONLY

    fun byteCount(channel: String, text: String): Int {
        if (text.isBlank()) return 0
        return if (channelType(channel) in setOf("group", "own"))
            Bytes.utf8(Protocol.normalize(text.trim())).size + Crypto.FRAME_OVERHEAD
        else Protocol.frameLenPlain(Protocol.compose(text, callsign))
    }

    val maxBytes: Int get() = Protocol.profile.maxPayload

    // -- sending ---------------------------------------------------------------------------

    fun send(channel: String, text0: String, signed: Boolean = false): Msg {
        val r = radio ?: throw KeyException("not connected to a radio")
        val ctype = channelType(channel)
        val text = text0.trim()
        if (ctype in READ_ONLY) throw KeyException("'$channel' is read-only - switch to public, a group or your own stream")
        if (locked && ctype in setOf("group", "own")) throw LockedException("identity is locked - unlock first")
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

    fun announce(group: String): Msg {
        val r = radio ?: throw KeyException("not connected to a radio")
        val frame = synchronized(lock) { keys.makeJoin(group) }
        val job = r.send(frame)
        val m = Msg(Crypto.nowSeconds(), "tx", "join", group, handle, "announcing myself", size = frame.size,
            job = job, status = "queued", airtime = Protocol.airtimeMs(frame.size), group = true)
        pending[job] = m
        return m
    }

    private fun onTx(r: TxResult) {
        val m = pending.remove(r.jobId) ?: return
        m.status = if (r.ok) "sent" else "failed"
        m.row?.let { log.setTxOk(it, r.ok) }
        onUpdate(m)
    }

    // -- receiving -------------------------------------------------------------------------

    private fun onPacket(pkt: Packet) {
        if (locked && Protocol.decode(pkt.payload).kind == "reserved") {
            val row = log.add("rx", pkt.payload, "pending", null, pkt.rssi, pkt.snr, ts = pkt.t)
            logged()
            synchronized(lock) { buffer.add(row to pkt) }
            onMsg(Msg(pkt.t, "rx", "pending", "other", null, "[${pkt.payload.size} B private - unlock to read]",
                pkt.rssi, pkt.snr, pkt.payload.size))
            return
        }
        val m = classify(pkt)
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
            val status = if (r.direction == "tx") (if (r.txOk == 1) "sent" else "failed") else null
            out.add(Msg(r.ts, r.direction, r.kind ?: "plain",
                channel ?: (if (r.kind == "plain") "public" else "other"), name, text, r.rssi, r.snr, r.payload.size,
                status = status, airtime = r.airtimeMs, verified = flag == "v", group = flag == "g", row = r.id))
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
