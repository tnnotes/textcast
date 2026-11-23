package io.github.textcast.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.text.Editable
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.TextWatcher
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.AbsListView
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import io.github.textcast.R
import io.github.textcast.core.KeyException
import io.github.textcast.core.Msg
import io.github.textcast.core.Node
import io.github.textcast.core.PayloadTooLong
import io.github.textcast.core.Protocol
import io.github.textcast.core.WrongPassphrase
import java.text.DateFormat
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/**
 * Chat screen, compact log style: every message left-aligned with a coloured
 * sender line, then the text. Own messages have an accent stripe; system and
 * join notes are small centred pills; a date line separates days.
 */
class ChatActivity : Activity(), Session.Listener {
    private lateinit var channel: Spinner
    private lateinit var status: TextView
    private lateinit var list: ListView
    private lateinit var empty: TextView
    private lateinit var input: EditText
    private lateinit var count: TextView
    private lateinit var signed: TextView
    private lateinit var sendBtn: ImageButton

    private var channels: List<Pair<String, String>> = emptyList()
    private var current = "all"
    private val rows = ArrayList<Row>()
    private val adapter = MsgAdapter()
    private val timeFmt = SimpleDateFormat("HH:mm", Locale.getDefault())
    private val tick = object : Runnable {
        override fun run() { updateStatus(); session.main.postDelayed(this, 5000) }
    }

    private val node: Node? get() = session.node

    /** A list row: a date line or a message. */
    private sealed class Row {
        class Day(val label: String) : Row()
        class Item(val m: Msg) : Row()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (node == null) { startActivity(Intent(this, LoginActivity::class.java)); finish(); return }
        setContentView(R.layout.activity_chat)
        status = findViewById(R.id.status)
        list = findViewById(R.id.messages)
        empty = findViewById(R.id.empty)
        input = findViewById(R.id.input)
        count = findViewById(R.id.count)
        signed = findViewById(R.id.signed)
        sendBtn = findViewById(R.id.send)
        list.adapter = adapter
        list.emptyView = empty
        current = savedInstanceState?.getString("channel") ?: "all"

        // channel picker in the title bar
        val bar = actionBar
        channel = Spinner(bar?.themedContext ?: this)
        if (bar != null) {
            bar.setDisplayShowTitleEnabled(false)
            bar.setDisplayShowCustomEnabled(true)
            bar.customView = channel
        }
        channel.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                val c = channels.getOrNull(pos)?.first ?: "all"
                if (c != current) { current = c; channelChanged() }
            }
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }

        input.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) = updateCount()
        })
        sendBtn.setOnClickListener { send() }
        signed.setOnClickListener { signed.isSelected = !signed.isSelected }
        list.setOnItemLongClickListener { _, _, pos, _ ->
            (rows.getOrNull(pos) as? Row.Item)?.let { messageMenu(it.m) }; true
        }

        session.startProblems.takeIf { it.isNotEmpty() }?.let {
            session.addSystem("radio check: " + it.joinToString("; "))
        }
        refreshChannels()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("channel", current)
    }

    override fun onResume() {
        super.onResume()
        if (node == null) { startActivity(Intent(this, LoginActivity::class.java)); finish(); return }
        session.addListener(this)
        session.visible = true
        session.unread = 0
        RadioService.update(this, 0, null)
        refreshChannels()
        session.main.post(tick)
    }

    override fun onPause() {
        session.removeListener(this)
        session.visible = false
        session.main.removeCallbacks(tick)
        super.onPause()
    }

    // -- session events -------------------------------------------------------------------

    override fun onMessage(m: Msg) {
        if (matches(m)) { appendRow(m); adapter.notifyDataSetChanged() }
        if (m.kind == "join") refreshChannels()
    }

    override fun onUpdate(m: Msg) { adapter.notifyDataSetChanged(); updateStatus() }

    override fun onLog(text: String) = rebuild()

    override fun onState() {
        if (node == null) { startActivity(Intent(this, LoginActivity::class.java)); finish() } else refreshChannels()
    }

    // -- channels and messages --------------------------------------------------------------

    private fun channelLabel(c: Pair<String, String>): String = when (c.second) {
        "all" -> "All"
        "public" -> "Public"
        "other" -> "Other"
        "group" -> "👥 ${c.first}"
        "own" -> "📣 ${c.first}"
        "follow" -> "👁 ${c.first}"
        else -> c.first
    }

    private fun refreshChannels() {
        val n = node ?: return
        channels = n.channels()
        val a = ArrayAdapter(channel.context, R.layout.title_spinner_item, channels.map { channelLabel(it) })
        a.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        channel.adapter = a
        val idx = channels.indexOfFirst { it.first == current }.let { if (it < 0) 0 else it }
        current = channels.getOrNull(idx)?.first ?: "all"
        channel.setSelection(idx)
        channelChanged()
    }

    private fun matches(m: Msg): Boolean = current == "all" || m.channel == current || m.direction == "sys"

    private fun dayKey(ts: Double): Int {
        val c = Calendar.getInstance().apply { timeInMillis = (ts * 1000).toLong() }
        return c.get(Calendar.YEAR) * 1000 + c.get(Calendar.DAY_OF_YEAR)
    }

    private fun dayLabel(ts: Double): String {
        val k = dayKey(ts)
        val now = System.currentTimeMillis() / 1000.0
        return when (k) {
            dayKey(now) -> "Today"
            dayKey(now - 86400) -> "Yesterday"
            else -> DateFormat.getDateInstance(DateFormat.MEDIUM).format(Date((ts * 1000).toLong()))
        }
    }

    private var lastDay = -1

    private fun appendRow(m: Msg) {
        val k = dayKey(m.ts)
        if (k != lastDay) { rows.add(Row.Day(dayLabel(m.ts))); lastDay = k }
        rows.add(Row.Item(m))
    }

    private fun rebuild() {
        rows.clear()
        lastDay = -1
        session.messages.filter { matches(it) }.forEach { appendRow(it) }
        adapter.notifyDataSetChanged()
        list.setSelection(rows.size - 1)
    }

    private fun emptyHint(type: String): String {
        val p = Protocol.profile
        return when (type) {
            "all" -> "No messages yet.\nEverything heard on ${String.format(Locale.ROOT, "%.3f", p.freqMhz)} MHz appears here."
            "public" -> "No public messages yet.\nPublic messages are plain text: anyone in range can read them."
            "group" -> "No messages in $current yet.\nMessages here are sealed: only members can read them."
            "own" -> "Nothing posted to $current yet.\nFollowers with the stream string can read your posts."
            "follow" -> "Nothing from $current yet.\nYou can read this stream but not post to it."
            else -> "Packets that nobody here can decode appear here."
        }
    }

    private fun channelChanged() {
        val n = node ?: return
        val type = n.channelType(current)
        val canSend = n.canSend(current)
        input.isEnabled = canSend
        input.hint = when (type) {
            "all", "public" -> "Message to everyone"
            "group" -> "Message to $current  🔒"
            "own" -> "Post to $current  🔒"
            "follow" -> "Read only"
            else -> "Read only"
        }
        empty.text = emptyHint(type)
        signed.visibility = if (type == "group" || type == "own") View.VISIBLE else View.GONE
        if (signed.visibility == View.GONE) signed.isSelected = false
        rebuild()
        updateCount()
        updateStatus()
    }

    private fun updateCount() {
        val n = node ?: return
        val used = n.byteCount(current, input.text.toString())
        val max = n.maxBytes
        sendBtn.isEnabled = used > 0 && n.canSend(current)
        sendBtn.alpha = if (sendBtn.isEnabled) 1f else 0.35f
        if (used >= max * 0.7) {
            count.visibility = View.VISIBLE
            count.text = if (used > max) "$used / $max bytes - too long" else "$used / $max bytes"
            count.setTextColor(color(if (used > max) R.color.text_error else R.color.text_meta))
        } else count.visibility = View.GONE
    }

    private fun updateStatus() {
        val n = node ?: return
        val p = Protocol.profile
        val st = n.stats()
        val parts = ArrayList<String>()
        parts.add((if (n.locked) "🔒 " else "") + (n.handle ?: n.nick ?: ""))
        if (n.radio == null) parts.add("offline")
        else {
            parts.add("${p.name} ${String.format(Locale.ROOT, "%.3f", p.freqMhz)} MHz")
            if (st.dutyLimitPct > 0) parts.add("duty ${String.format(Locale.ROOT, "%.1f", st.dutyPct)}/${fmt(st.dutyLimitPct)} %")
        }
        if (st.queue > 0) parts.add("${st.queue} waiting" + if (st.waitS >= 1) " ${st.waitS.toInt()} s" else "")
        status.text = parts.joinToString("  ·  ")
        val warn = n.radio == null || n.locked || st.queue > 0
        status.setBackgroundColor(color(if (warn) R.color.status_warn_bg else R.color.status_bg))
    }

    private fun fmt(v: Double) = if (v == Math.floor(v)) v.toLong().toString() else v.toString()

    private fun send() {
        val n = node ?: return
        val text = input.text.toString().trim()
        if (text.isEmpty()) return
        try {
            val m = n.send(current, text, signed.isSelected)
            session.addMessage(m)
            input.setText("")
            rebuild()
            updateStatus()
        } catch (e: PayloadTooLong) {
            toast("Too long: ${e.text()}")
        } catch (e: KeyException) {
            toast(e.text())
        } catch (e: Exception) {
            toast(e.text())
        }
    }

    private fun messageMenu(m: Msg) {
        val items = mutableListOf("Copy text")
        if (m.sender != null && m.direction == "rx") items.add("Reply to ${m.sender}")
        AlertDialog.Builder(this).setItems(items.toTypedArray()) { _, which ->
            when (which) {
                0 -> copyText("message", m.text)
                1 -> { input.setText("@${m.sender?.substringBefore('#')} "); input.setSelection(input.text.length) }
            }
        }.show()
    }

    // -- rows ---------------------------------------------------------------------------------

    private val senderColors by lazy {
        listOf(R.color.sender_0, R.color.sender_1, R.color.sender_2, R.color.sender_3,
            R.color.sender_4, R.color.sender_5, R.color.sender_6, R.color.sender_7).map { color(it) }
    }

    /** The same name always gets the same colour. */
    private fun senderColor(name: String): Int {
        var h = 0
        for (c in name.lowercase()) h = h * 31 + c.code
        return senderColors[Math.abs(h % senderColors.size)]
    }

    private class MsgHolder(val stripe: View, val text: TextView)

    private inner class MsgAdapter : BaseAdapter() {
        override fun getCount() = rows.size
        override fun getItem(position: Int): Any = rows[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getViewTypeCount() = 2
        override fun getItemViewType(position: Int): Int {
            val r = rows[position]
            return if (r is Row.Item && r.m.direction != "sys" && r.m.kind != "join") 0 else 1
        }

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val r = rows[position]
            return if (getItemViewType(position) == 0) messageView((r as Row.Item).m, convertView)
            else pillView(r, convertView)
        }
    }

    private fun messageView(m: Msg, convertView: View?): View {
        val row: LinearLayout
        val h: MsgHolder
        if (convertView?.tag is MsgHolder) {
            row = convertView as LinearLayout
            h = convertView.tag as MsgHolder
        } else {
            row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = AbsListView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            }
            val stripe = View(this)
            row.addView(stripe, LinearLayout.LayoutParams(dp(3), ViewGroup.LayoutParams.MATCH_PARENT))
            val tv = TextView(this).apply {
                setPadding(dp(10), dp(6), dp(10), dp(7))
                textSize = 15f
                setLineSpacing(dp(2).toFloat(), 1f)
            }
            row.addView(tv, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            h = MsgHolder(stripe, tv)
            row.tag = h
        }
        val mine = m.direction == "tx"
        row.setBackgroundResource(if (mine) R.drawable.bg_row_me else 0)
        h.stripe.setBackgroundColor(if (mine) color(R.color.accent) else 0)
        row.setPadding(0, dp(1), 0, dp(1))
        h.text.text = render(m)
        return row
    }

    private fun pillView(r: Row, convertView: View?): View {
        val box = (convertView as? LinearLayout)?.takeIf { it.tag == "pill" } ?: LinearLayout(this).apply {
            tag = "pill"
            setGravity(Gravity.CENTER_HORIZONTAL)
            setPadding(dp(24), dp(6), dp(24), dp(6))
            addView(TextView(this@ChatActivity).apply {
                setBackgroundResource(R.drawable.bg_pill)
                setPadding(dp(12), dp(3), dp(12), dp(3))
                textSize = 12f
                gravity = Gravity.CENTER
                setTextColor(color(R.color.text_meta))
            })
        }
        val tv = box.getChildAt(0) as TextView
        tv.text = when (r) {
            is Row.Day -> r.label
            is Row.Item -> timeFmt.format(Date((r.m.ts * 1000).toLong())) + "  " +
                (if (r.m.kind == "join") "👥 ${r.m.channel}: " else "") + r.m.text
        }
        tv.setTypeface(null, if (r is Row.Day) Typeface.BOLD else Typeface.NORMAL)
        return box
    }

    private fun span(sb: SpannableStringBuilder, text: String, vararg spans: Any) {
        val start = sb.length
        sb.append(text)
        for (s in spans) sb.setSpan(s, start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    }

    /** Line 1: sender, type icons, time and signal. Line 2: the text. */
    private fun render(m: Msg): CharSequence {
        val sb = SpannableStringBuilder()
        val meta = color(R.color.text_meta)
        val who = when {
            m.direction == "tx" -> "me"
            m.sender != null -> m.sender
            m.kind == "unverified" -> "unverified"
            else -> null
        }
        val whoColor = when {
            m.direction == "tx" -> color(R.color.accent)
            m.kind == "unverified" -> color(R.color.text_warn)
            who != null -> senderColor(who)
            else -> meta
        }
        if (who != null) span(sb, who, StyleSpan(Typeface.BOLD), ForegroundColorSpan(whoColor))
        if (m.verified && m.direction == "rx") span(sb, " ✓", ForegroundColorSpan(color(R.color.text_sealed)))
        val icons = StringBuilder()
        if (current == "all" && m.channel != "public" && m.channel != "other" && m.channel != "all") icons.append("  ${m.channel}")
        when (m.kind) {
            "sealed" -> icons.append(if (m.group) "  👥🔒" else "  🔒")
            "signed" -> icons.append("  ✍")
        }
        if (icons.isNotEmpty()) span(sb, icons.toString(), ForegroundColorSpan(meta), RelativeSizeSpan(0.85f))
        val tail = ArrayList<String>()
        tail.add(timeFmt.format(Date((m.ts * 1000).toLong())))
        if (m.direction == "rx" && m.rssi != null) tail.add("${m.rssi} dBm" + (m.snr?.let { "  SNR $it" } ?: ""))
        span(sb, (if (sb.isEmpty()) "" else "   ") + tail.joinToString("  ·  "), ForegroundColorSpan(meta), RelativeSizeSpan(0.8f))
        if (m.direction == "tx") {
            val (label, c) = when (m.status) {
                "sent" -> "  ·  ✓ sent" to meta
                "failed" -> "  ·  ✗ not sent" to color(R.color.text_error)
                else -> "  ·  sending…" to meta
            }
            span(sb, label, ForegroundColorSpan(c), RelativeSizeSpan(0.8f))
        }
        sb.append("\n")
        val dim = m.kind in setOf("undecoded", "locked", "pending")
        span(sb, m.text, ForegroundColorSpan(if (dim) meta else color(R.color.text_plain)))
        return sb
    }

    // -- menu ---------------------------------------------------------------------------------

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, M_KEYS, 0, R.string.menu_keys)
        menu.add(0, M_ADD, 1, R.string.menu_add_key)
        menu.add(0, M_ID, 2, R.string.menu_identity)
        menu.add(0, M_CONTACTS, 3, R.string.menu_contacts)
        menu.add(0, M_RADIO, 4, R.string.radio_settings)
        menu.add(0, M_LOCK, 5, R.string.menu_lock)
        menu.add(0, M_PASSWD, 6, R.string.menu_passwd)
        menu.add(0, M_EXPORT, 7, R.string.menu_export)
        menu.add(0, M_LOGOUT, 8, R.string.menu_disconnect)
        return true
    }

    override fun onPrepareOptionsMenu(menu: Menu): Boolean {
        val n = node ?: return false
        menu.findItem(M_LOCK).setTitle(if (n.locked) R.string.menu_unlock else R.string.menu_lock).isVisible = n.protected
        menu.findItem(M_PASSWD).isVisible = n.protected && !n.locked
        for (id in listOf(M_KEYS, M_ADD, M_ID, M_CONTACTS, M_EXPORT)) menu.findItem(id).isEnabled = !n.locked
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        val n = node ?: return true
        when (item.itemId) {
            M_KEYS -> KeysUi(this, n) { refreshChannels() }.showKeys()
            M_ADD -> KeysUi(this, n) { refreshChannels() }.addKey()
            M_ID -> KeysUi(this, n) { }.showIdentity()
            M_CONTACTS -> KeysUi(this, n) { }.showContacts()
            M_RADIO -> startActivity(Intent(this, RadioSettingsActivity::class.java))
            M_LOCK -> if (n.locked) unlock(n) else { n.lockIdentity(); refreshChannels(); invalidateOptionsMenu() }
            M_PASSWD -> askNewPassphrase("Change passphrase", null) { p ->
                bg({ n.keys.changePassphrase(p) }) { r -> r.onSuccess { toast("Passphrase changed") }.onFailure { toast(it.text()) } }
            }
            M_EXPORT -> exportIdentity(n)
            M_LOGOUT -> confirm("Log out", "Stop listening and close the identity?", "Log out") {
                session.disconnect()
                startActivity(Intent(this, LoginActivity::class.java))
                finish()
            }
        }
        return true
    }

    private fun unlock(n: Node) {
        val pw = field("Passphrase", password = true)
        form("Unlock", null, listOf(pw), "Unlock") {
            val p = pw.text.toString()
            bg({ n.unlock(p) }) { r ->
                r.onSuccess {
                    session.messages.clear(); session.messages.addAll(n.history())
                    refreshChannels(); invalidateOptionsMenu()
                }.onFailure { e -> toast(if (e is WrongPassphrase) "Wrong passphrase" else e.text()) }
            }
            null
        }
    }

    private var exportBlob: ByteArray? = null

    private fun exportIdentity(n: Node) {
        askNewPassphrase("Export identity",
            "The file is protected with this passphrase. Move, don't clone: use the identity on one device at a time.") { p ->
            bg({ n.keys.exportBlob(p) }) { r ->
                r.onSuccess { blob ->
                    exportBlob = blob
                    val i = Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                        .setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE, "${n.nick ?: "identity"}.ltid")
                    startActivityForResult(i, REQ_EXPORT)
                }.onFailure { toast(it.text()) }
            }
        }
    }

    @Deprecated("Activity API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_EXPORT && resultCode == RESULT_OK) {
            val uri = data?.data ?: return
            val blob = exportBlob ?: return
            try {
                contentResolver.openOutputStream(uri)!!.use { it.write(blob) }
                toast("Identity exported")
            } catch (e: Exception) { toast("Export failed: ${e.text()}") }
            exportBlob = null
        }
    }

    companion object {
        private const val M_KEYS = 1
        private const val M_ADD = 2
        private const val M_ID = 3
        private const val M_CONTACTS = 4
        private const val M_RADIO = 5
        private const val M_LOCK = 6
        private const val M_PASSWD = 7
        private const val M_EXPORT = 8
        private const val M_LOGOUT = 9
        private const val REQ_EXPORT = 20
    }
}
