package io.github.textcast.core

import java.io.File

/**
 * Message log: one JSON object per line (append-only; updates are appended as
 * {"u": id, ...} records and folded in on load). Private text, channel and
 * sender are stored encrypted ("enc1:...") by the Node, as on the desktop.
 * The file is compacted when pruned.
 */
class MessageLog(private val file: File) {
    class Row(
        val id: Long, val ts: Double, val direction: String, var kind: String?, val payload: ByteArray,
        var text: String?, var channel: String?, var sender: String?, val rssi: Int?, val snr: Int?,
        val airtimeMs: Double?, var txOk: Int?,
    )

    private val rows = ArrayList<Row>()
    private var nextId = 1L

    init { load() }

    private fun rowFrom(m: Map<String, Any?>): Row? = try {
        Row((m["id"] as Number).toLong(), (m["ts"] as Number).toDouble(), m.str("dir") ?: "rx", m.str("kind"),
            Bytes.unhex(m.str("payload") ?: ""), m.str("text"), m.str("channel"), m.str("sender"),
            (m["rssi"] as? Number)?.toInt(), (m["snr"] as? Number)?.toInt(), (m["airtime_ms"] as? Number)?.toDouble(),
            (m["tx_ok"] as? Number)?.toInt())
    } catch (e: Exception) { null }

    private fun toMap(r: Row): Map<String, Any?> = linkedMapOf(
        "id" to r.id, "ts" to r.ts, "dir" to r.direction, "kind" to r.kind, "payload" to Bytes.hex(r.payload),
        "text" to r.text, "channel" to r.channel, "sender" to r.sender, "rssi" to r.rssi?.toLong(),
        "snr" to r.snr?.toLong(), "airtime_ms" to r.airtimeMs, "tx_ok" to r.txOk?.toLong())

    @Synchronized private fun load() {
        rows.clear()
        if (!file.exists()) return
        val byId = HashMap<Long, Row>()
        file.forEachLine { line ->
            if (line.isBlank()) return@forEachLine
            val m = try { Json.parseObject(line) } catch (e: Exception) { return@forEachLine }
            val u = (m["u"] as? Number)?.toLong()
            if (u != null) {
                byId[u]?.let { r ->
                    if ("kind" in m) r.kind = m.str("kind")
                    if ("text" in m) r.text = m.str("text")
                    if ("channel" in m) r.channel = m.str("channel")
                    if ("sender" in m) r.sender = m.str("sender")
                    if ("tx_ok" in m) r.txOk = (m["tx_ok"] as? Number)?.toInt()
                }
            } else rowFrom(m)?.let { rows.add(it); byId[it.id] = it }
        }
        nextId = (rows.maxOfOrNull { it.id } ?: 0) + 1
    }

    private fun append(m: Map<String, Any?>) = file.appendText(Json.stringify(m) + "\n")

    @Synchronized fun add(direction: String, payload: ByteArray, kind: String?, text: String?,
                          rssi: Int? = null, snr: Int? = null, airtimeMs: Double? = null, ts: Double = Crypto.nowSeconds(),
                          channel: String? = null, sender: String? = null): Long {
        val r = Row(nextId++, ts, direction, kind, payload, text, channel, sender, rssi, snr, airtimeMs, null)
        rows.add(r)
        append(toMap(r))
        return r.id
    }

    @Synchronized private fun find(id: Long): Row? = rows.lastOrNull { it.id == id }

    @Synchronized fun setTxOk(id: Long, ok: Boolean) = setStatus(id, if (ok) 1 else 0)

    /** tx_ok: 0 not sent, 1 sent, 2 delivered, 3 not confirmed (direct messages). */
    @Synchronized fun setStatus(id: Long, code: Int) {
        val r = find(id) ?: return
        r.txOk = code
        append(linkedMapOf("u" to id, "tx_ok" to code.toLong()))
    }

    @Synchronized fun update(id: Long, kind: String?, text: String?, channel: String?, sender: String?) {
        val r = find(id) ?: return
        r.kind = kind; r.text = text; r.channel = channel; r.sender = sender
        append(linkedMapOf("u" to id, "kind" to kind, "text" to text, "channel" to channel, "sender" to sender))
    }

    /** Newest [n] rows, oldest first; optionally only one kind. */
    @Synchronized fun rows(n: Int, kind: String? = null): List<Row> =
        (if (kind == null) rows else rows.filter { it.kind == kind }).takeLast(n)

    @Synchronized fun count(): Int = rows.size

    /** Keep at most maxRows rows and maxDays days (0 = no limit). Returns rows removed. */
    @Synchronized fun prune(maxRows: Int, maxDays: Double): Int {
        val before = rows.size
        if (maxDays > 0) {
            val cutoff = Crypto.nowSeconds() - maxDays * 86400
            rows.removeAll { it.ts < cutoff }
        }
        if (maxRows > 0 && rows.size > maxRows) rows.subList(0, rows.size - maxRows).clear()
        val removed = before - rows.size
        if (removed > 0 || (file.exists() && file.length() > 64 * 1024 && removed == 0 && before > 0 && needsCompact())) compact()
        return removed
    }

    private fun needsCompact(): Boolean = file.length() > rows.size * 600L + 64 * 1024

    @Synchronized fun compact() {
        val tmp = File(file.path + ".tmp")
        tmp.bufferedWriter().use { w -> for (r in rows) { w.write(Json.stringify(toMap(r))); w.write("\n") } }
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }

    @Synchronized fun clear() { rows.clear(); file.delete() }
}
