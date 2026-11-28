package io.github.textcast.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
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
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import io.github.textcast.R
import io.github.textcast.core.KeyException
import io.github.textcast.core.Keyring
import io.github.textcast.core.Msg
import io.github.textcast.core.Node
import io.github.textcast.core.Numbers
import io.github.textcast.core.PayloadTooLong
import java.text.DateFormat
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/**
 * One conversation, like a phone messenger: their messages on the left (with the
 * sender's name in Public and groups), mine on the right with ✓ sent and ✓✓
 * delivered. Tap a message that was not delivered to send it again; long-press
 * for details. The Info menu shows the person's number or the group's invite.
 */
class ChatActivity : Activity(), Session.Listener {
    private lateinit var status: TextView
    private lateinit var list: ListView
    private lateinit var empty: TextView
    private lateinit var input: EditText
    private lateinit var count: TextView
    private lateinit var signed: TextView
    private lateinit var sendBtn: ImageButton

    private var current = "public"
    private val rows = ArrayList<Row>()
    private val adapter = MsgAdapter()
    private val timeFmt = SimpleDateFormat("HH:mm", Locale.getDefault())
    private val tick = object : Runnable {
        override fun run() { updateStatus(); session.main.postDelayed(this, 3000) }
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
        list.setBackgroundColor(color(R.color.chat_wall))
        current = savedInstanceState?.getString("channel") ?: intent.getStringExtra(EXTRA_CHANNEL) ?: "public"
        actionBar?.setDisplayHomeAsUpEnabled(true)

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
        list.setOnItemClickListener { _, _, pos, _ ->           // tap a "not delivered" message to resend
            (rows.getOrNull(pos) as? Row.Item)?.m?.takeIf { canResend(it) }?.let { resend(it) }
        }
        status.setOnClickListener { if (node?.radio == null) session.reattach() }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra(EXTRA_CHANNEL)?.let { current = it; channelChanged() }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("channel", current)
    }

    override fun onResume() {
        super.onResume()
        val n = node
        if (n == null) { startActivity(Intent(this, LoginActivity::class.java)); finish(); return }
        if (n.channels().none { it.first == current }) { finish(); return }      // deleted, or locked away
        session.addListener(this)
        session.visible = true
        session.openChannel = current
        session.unreadBy.remove(current)
        session.unread = 0
        RadioService.update(this, 0, null)
        channelChanged()
        session.main.post(tick)
    }

    override fun onPause() {
        session.removeListener(this)
        session.visible = false
        session.openChannel = null
        session.main.removeCallbacks(tick)
        super.onPause()
    }

    // -- session events -------------------------------------------------------------------

    override fun onMessage(m: Msg) {
        if (matches(m)) { appendRow(m); adapter.notifyDataSetChanged() }
        session.unreadBy.remove(current)
    }

    override fun onUpdate(m: Msg) { adapter.notifyDataSetChanged(); updateStatus() }

    override fun onLog(text: String) = rebuild()

    override fun onState() {
        val n = node
        if (n == null) { startActivity(Intent(this, LoginActivity::class.java)); finish(); return }
        if (n.channels().none { it.first == current }) { finish(); return }
        rebuild(); updateStatus()
    }

    // -- messages -----------------------------------------------------------------------------

    private fun matches(m: Msg): Boolean = m.channel == current || m.direction == "sys"

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
        if (k != lastDay && m.direction != "sys") { rows.add(Row.Day(dayLabel(m.ts))); lastDay = k }
        rows.add(Row.Item(m))
    }

    private fun rebuild() {
        rows.clear()
        lastDay = -1
        session.messages.filter { matches(it) }.forEach { appendRow(it) }
        adapter.notifyDataSetChanged()
        list.setSelection(rows.size - 1)
    }

    private fun canResend(m: Msg) = m.kind == "direct" && m.direction == "tx" && m.status in setOf("unconfirmed", "failed")

    private fun resend(m: Msg) {
        val n = node ?: return
        if (n.radio == null) { toast("Plug the radio in first"); return }
        try { n.resend(m); toast("Sending it again…"); adapter.notifyDataSetChanged() } catch (e: Exception) { toast(e.text()) }
    }

    private fun emptyHint(type: String): String {
        val who = node?.channelTitle(current) ?: current
        return when (type) {
            "direct" -> "🔒 Messages to $who are private: only $who's device can read them.\n\n✓✓ means $who's device got the message."
            "public" -> "📢 Everyone within radio range can read what you write here."
            "group" -> "🔒 Only members of $current can read messages here."
            "own" -> "Your channel: you post, people with its string read."
            "follow" -> "You can read this channel but not post to it."
            else -> ""
        }
    }

    private fun contact(): Keyring.ContactInfo? {
        val n = node ?: return null
        if (!current.startsWith("@") || n.locked) return null
        return n.keys.contactInfo().firstOrNull { "@" + it.handle == current }
    }

    private fun channelChanged() {
        val n = node ?: return
        val type = n.channelType(current)
        val canSend = n.canSend(current)
        actionBar?.title = if (type == "public") "Public" else n.channelTitle(current)
        actionBar?.subtitle = when (type) {
            "public" -> "Everyone nearby can read"
            "direct" -> "🔒 Private" + (contact()?.let { " · " + Numbers.format(it.number) } ?: "")
            "group" -> "🔒 Group"
            "own" -> "Your channel"
            else -> "Channel · read only"
        }
        input.isEnabled = canSend
        (input.parent as? View)?.visibility = if (canSend) View.VISIBLE else View.GONE
        input.hint = when (type) {
            "public" -> "Message everyone nearby"
            "direct" -> "Private message"
            else -> "Message"
        }
        empty.text = emptyHint(type)
        signed.visibility = if (type == "group" || type == "own") View.VISIBLE else View.GONE
        if (signed.visibility == View.GONE) signed.isSelected = false
        rebuild()
        updateCount()
        updateStatus()
        invalidateOptionsMenu()
    }

    private fun updateCount() {
        val n = node ?: return
        val used = n.byteCount(current, input.text.toString())
        val max = n.maxBytes
        sendBtn.isEnabled = used in 1..max && n.canSend(current)
        sendBtn.alpha = if (sendBtn.isEnabled) 1f else 0.35f
        if (used >= max * 0.7) {
            count.visibility = View.VISIBLE
            count.text = if (used > max) "$used / $max bytes - too long for one radio message" else "$used / $max bytes"
            count.setTextColor(color(if (used > max) R.color.text_error else R.color.text_meta))
        } else count.visibility = View.GONE
    }

    /** Shown only when something needs attention: radio unplugged, messages waiting. */
    private fun updateStatus() {
        val n = node ?: return
        val st = n.stats()
        val text: String? = when {
            session.reconnecting -> "Reconnecting the radio…"
            n.radio == null -> if (session.deviceLabel == null) "No radio · plug it in to send" else "Radio unplugged · plug it back in"
            st.queue > 0 -> "${st.queue} waiting to send" + (if (st.waitS >= 1) " (about ${st.waitS.toInt()} s)" else "")
            else -> null
        }
        status.visibility = if (text == null) View.GONE else View.VISIBLE
        status.text = text ?: ""
        status.setBackgroundColor(color(R.color.status_warn_bg))
    }

    private fun send() {
        val n = node ?: return
        val text = input.text.toString().trim()
        if (text.isEmpty()) return
        if (n.radio == null) { toast("Plug the radio in first"); return }
        try {
            val m = n.send(current, text, signed.isSelected)
            session.addMessage(m)
            input.setText("")
            rebuild()
            updateStatus()
        } catch (e: PayloadTooLong) {
            toast("Too long for one radio message: shorten it")
        } catch (e: KeyException) {
            toast(e.text())
        } catch (e: Exception) {
            toast(e.text())
        }
    }

    private fun details(m: Msg): String {
        val parts = ArrayList<String>()
        parts.add(DateFormat.getDateTimeInstance().format(Date((m.ts * 1000).toLong())))
        if (m.rssi != null) parts.add("Signal ${m.rssi} dBm" + (m.snr?.let { " · SNR $it dB" } ?: ""))
        parts.add("${m.size} bytes" + (m.airtime?.let { " · ${it.toInt()} ms on air" } ?: ""))
        if (m.kind == "signed") parts.add("Signed")
        if (m.verified && m.direction == "rx" && !m.group) parts.add("✓ sent with a key you hold")
        return parts.joinToString("\n")
    }

    private fun messageMenu(m: Msg) {
        if (m.direction == "sys") return
        val items = mutableListOf("Copy text", "Details")
        if (canResend(m)) items.add(0, "Send again")
        AlertDialog.Builder(this).setItems(items.toTypedArray()) { _, which ->
            when (items[which]) {
                "Send again" -> resend(m)
                "Copy text" -> copyText("message", m.text)
                else -> info("Message", details(m))
            }
        }.show()
    }

    // -- info about the chat ---------------------------------------------------------------------

    private fun showInfo() {
        val n = node ?: return
        val type = n.channelType(current)
        when (type) {
            "direct" -> {
                val c = contact()
                val msg = (c?.let {
                    "TextCast number ${Numbers.format(it.number)}\n\n" +
                        (if (it.verified) "✓ Added with their identity string."
                         else "Added by number. To be sure it's really them, check the number with them.")
                } ?: "") + "\n\nName on the radio: ${current.drop(1)}"
                AlertDialog.Builder(this).setTitle(n.channelTitle(current)).setMessage(msg)
                    .setPositiveButton("Rename") { _, _ -> rename(n) }
                    .setNeutralButton("Delete") { _, _ ->
                        confirm("Delete ${n.channelTitle(current)}", "Remove this person? You can add them again with their number.", "Delete") {
                            n.contactDel(current.drop(1)); finish()
                        }
                    }
                    .setNegativeButton("Close", null).show()
            }
            "public" -> info("Public", "Everyone within radio range, with TextCast on the same settings, reads the Public chat. " +
                "Don't write anything private here: use a private chat (+ and their number).\n\nYou appear as ${n.callsign ?: ""}.")
            else -> KeysUi(this, n, { if (n.channels().none { it.first == current }) finish() else channelChanged() })
                .keyActions(current, type)
        }
    }

    private fun rename(n: Node) {
        val f = field("Name", value = n.channelTitle(current))
        form("Rename", null, listOf(f), "Save") {
            try { n.keys.renameContact(current.drop(1), f.text.toString()); channelChanged(); null } catch (e: KeyException) { e.text() }
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, M_INFO, 0, R.string.menu_info).setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            android.R.id.home -> finish()
            M_INFO -> showInfo()
        }
        return true
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

    private class MsgHolder(val box: LinearLayout, val text: TextView)

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

    private fun bubble(mine: Boolean) = GradientDrawable().apply {
        cornerRadius = dp(12).toFloat()
        setColor(color(if (mine) R.color.bubble_mine else R.color.bubble_them))
    }

    private fun messageView(m: Msg, convertView: View?): View {
        val h: MsgHolder
        if (convertView?.tag is MsgHolder) h = convertView.tag as MsgHolder
        else {
            val box = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = AbsListView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                setPadding(dp(4), dp(2), dp(4), dp(2))
            }
            val tv = TextView(this).apply {
                setPadding(dp(11), dp(6), dp(11), dp(6))
                textSize = 15.5f
                maxWidth = (resources.displayMetrics.widthPixels * 0.8).toInt()
                setTextColor(color(R.color.text_plain))
            }
            box.addView(tv, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            h = MsgHolder(box, tv)
            box.tag = h
        }
        val mine = m.direction == "tx"
        h.box.gravity = if (mine) Gravity.END else Gravity.START
        h.text.background = bubble(mine)
        h.text.text = render(m)
        return h.box
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
            is Row.Item -> (if (r.m.kind == "join") "👥 " else "") + r.m.text
        }
        tv.setTypeface(null, if (r is Row.Day) Typeface.BOLD else Typeface.NORMAL)
        return box
    }

    private fun span(sb: SpannableStringBuilder, text: String, vararg spans: Any) {
        val start = sb.length
        sb.append(text)
        for (s in spans) sb.setSpan(s, start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    }

    /** Sender (Public and groups only), the text, then the time and ticks. */
    private fun render(m: Msg): CharSequence {
        val sb = SpannableStringBuilder()
        val meta = color(R.color.text_meta)
        val type = node?.channelType(current) ?: "public"
        if (m.direction == "rx" && type in setOf("public", "group")) {
            val who = m.sender ?: if (m.kind == "unverified") "unverified" else "?"
            span(sb, who, StyleSpan(Typeface.BOLD), ForegroundColorSpan(senderColor(who)), RelativeSizeSpan(0.85f))
            if (m.verified && !m.group) span(sb, " ✓", ForegroundColorSpan(color(R.color.text_sealed)), RelativeSizeSpan(0.85f))
            sb.append("\n")
        }
        val dim = m.kind in setOf("undecoded", "locked", "pending", "unverified")
        span(sb, m.text, ForegroundColorSpan(if (dim) meta else color(R.color.text_plain)))
        span(sb, "  " + timeFmt.format(Date((m.ts * 1000).toLong())), ForegroundColorSpan(meta), RelativeSizeSpan(0.75f))
        if (m.direction == "tx") {
            val direct = m.kind == "direct"
            val (label, c) = when (m.status) {
                "sent" -> " ✓" to meta
                "delivered" -> " ✓✓" to color(R.color.accent)
                "unconfirmed" -> " ✓\nnot delivered · tap to resend" to color(R.color.text_warn)
                "failed" -> (if (direct) " ⚠\nnot sent · tap to resend" else " ⚠ not sent") to color(R.color.text_error)
                else -> " 🕓" to meta
            }
            span(sb, label, ForegroundColorSpan(c), RelativeSizeSpan(0.75f))
        }
        return sb
    }

    companion object {
        const val EXTRA_CHANNEL = "channel"
        private const val M_INFO = 1
    }
}
