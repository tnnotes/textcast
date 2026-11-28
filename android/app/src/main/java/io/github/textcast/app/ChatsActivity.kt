package io.github.textcast.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.text.TextUtils
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.AbsListView
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import io.github.textcast.R
import io.github.textcast.core.KeyException
import io.github.textcast.core.Msg
import io.github.textcast.core.Node
import io.github.textcast.core.NumberException
import io.github.textcast.core.Numbers
import io.github.textcast.core.Protocol
import io.github.textcast.core.WrongPassphrase
import java.text.DateFormat
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/**
 * Home screen, like a phone messenger: a list of chats (Public first, then people
 * and groups by their latest message) with the last message and an unread count.
 * Tap a chat to open it; the round + button adds a person by their TextCast number.
 */
class ChatsActivity : Activity(), Session.Listener {
    private lateinit var status: TextView
    private lateinit var lockBar: TextView
    private lateinit var list: ListView
    private var chats: List<Pair<String, String>> = emptyList()
    private val adapter = ChatsAdapter()
    private val timeFmt = SimpleDateFormat("HH:mm", Locale.getDefault())
    private var lookupHandler: ((Node.LookupResult) -> Unit)? = null
    private val tick = object : Runnable {
        override fun run() { updateStatus(); session.main.postDelayed(this, 3000) }
    }

    private val node: Node? get() = session.node

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (node == null) { startActivity(Intent(this, LoginActivity::class.java)); finish(); return }
        val root = FrameLayout(this)
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        status = TextView(this).apply {
            setPadding(dp(16), dp(5), dp(16), dp(5))
            textSize = 13f
            setSingleLine(true)
            ellipsize = TextUtils.TruncateAt.END
            setTextColor(color(R.color.status_text))
            setOnClickListener { if (node?.radio == null) session.reattach() }
        }
        lockBar = TextView(this).apply {
            setPadding(dp(16), dp(10), dp(16), dp(10))
            textSize = 14f
            setBackgroundColor(color(R.color.status_warn_bg))
            text = "🔒 Locked · tap to unlock"
            visibility = View.GONE
            setOnClickListener { node?.let { unlock(it) } }
        }
        list = ListView(this).apply {
            divider = null
            adapter = this@ChatsActivity.adapter
            setOnItemClickListener { _, _, pos, _ -> chats.getOrNull(pos)?.let { session.openConversation(this@ChatsActivity, it.first) } }
            clipToPadding = false
            setPadding(0, 0, 0, dp(88))
        }
        col.addView(status, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        col.addView(lockBar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        col.addView(list, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(col, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        val fab = TextView(this).apply {
            text = "+"
            textSize = 30f
            gravity = Gravity.CENTER
            setTextColor(0xFFFFFFFF.toInt())
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color(R.color.accent)) }
            contentDescription = getString(R.string.new_chat)
            elevation = dp(6).toFloat()
            setOnClickListener { newChat() }
        }
        root.addView(fab, FrameLayout.LayoutParams(dp(60), dp(60), Gravity.BOTTOM or Gravity.END).apply {
            setMargins(0, 0, dp(20), dp(24))
        })
        setContentView(root)
        title = getString(R.string.app_name)
        session.startProblems.takeIf { it.isNotEmpty() }?.let { session.addSystem("radio check: " + it.joinToString("; ")) }
    }

    override fun onResume() {
        super.onResume()
        if (node == null) { startActivity(Intent(this, LoginActivity::class.java)); finish(); return }
        session.addListener(this)
        session.visible = true
        session.openChannel = null
        session.unread = 0
        RadioService.update(this, 0, null)
        refresh()
        session.main.post(tick)
    }

    override fun onPause() {
        session.removeListener(this)
        session.visible = false
        session.main.removeCallbacks(tick)
        super.onPause()
    }

    // -- session events -------------------------------------------------------------------

    override fun onMessage(m: Msg) = refresh()
    override fun onUpdate(m: Msg) = refresh()
    override fun onLog(text: String) = updateStatus()
    override fun onState() {
        if (node == null) { startActivity(Intent(this, LoginActivity::class.java)); finish() } else refresh()
    }
    override fun onLookup(r: Node.LookupResult) { lookupHandler?.invoke(r) }

    // -- list ------------------------------------------------------------------------------

    private fun refresh() {
        val n = node ?: return
        val all = n.channels().filter { it.second in CHAT_TYPES }
        chats = all.sortedWith(compareBy<Pair<String, String>>({ it.second != "public" },
            { -(session.lastOf(it.first)?.ts ?: 0.0) }, { n.channelTitle(it.first).lowercase() }))
        adapter.notifyDataSetChanged()
        actionBar?.subtitle = if (n.locked) "🔒 locked" else "My number ${Numbers.format(n.number)}"
        lockBar.visibility = if (n.locked) View.VISIBLE else View.GONE
        updateStatus()
        invalidateOptionsMenu()
    }

    private fun updateStatus() {
        val n = node ?: return
        val p = Protocol.profile
        val st = n.stats()
        val (text, warn) = when {
            session.reconnecting -> "Reconnecting the radio…" to true
            n.radio == null -> (if (session.deviceLabel == null) "No radio · plug it in to send"
                                else "Radio unplugged · plug it back in") to true
            st.queue > 0 -> "● Radio on · ${st.queue} waiting" + (if (st.waitS >= 1) " (about ${st.waitS.toInt()} s)" else "") to false
            else -> "● Radio on · ${String.format(Locale.ROOT, "%.1f", p.freqMhz)} MHz · SF${p.sf}" to false
        }
        status.text = text
        status.setBackgroundColor(color(if (warn) R.color.status_warn_bg else R.color.status_bg))
    }

    private fun chatTitle(c: Pair<String, String>): String =
        if (c.second == "public") "Public" else node?.channelTitle(c.first) ?: c.first

    private fun dayKey(ts: Double): Int {
        val c = Calendar.getInstance().apply { timeInMillis = (ts * 1000).toLong() }
        return c.get(Calendar.YEAR) * 1000 + c.get(Calendar.DAY_OF_YEAR)
    }

    private fun whenText(ts: Double): String = if (dayKey(ts) == dayKey(System.currentTimeMillis() / 1000.0))
        timeFmt.format(Date((ts * 1000).toLong())) else DateFormat.getDateInstance(DateFormat.SHORT).format(Date((ts * 1000).toLong()))

    private fun preview(c: Pair<String, String>, last: Msg?): String {
        if (last == null) return when (c.second) {
            "public" -> "Everyone nearby"
            "direct" -> "Private chat · tap to write"
            "group" -> "Group"
            "own" -> "Your channel"
            else -> "Channel"
        }
        return when {
            last.direction == "tx" && last.kind == "direct" -> (when (last.status) {
                "delivered" -> "✓✓ "; "sent" -> "✓ "; "queued" -> "🕓 "; else -> "⚠ " }) + last.text
            last.direction == "tx" -> "You: ${last.text}"
            c.second in setOf("public", "group") && last.sender != null -> "${last.sender}: ${last.text}"
            else -> last.text
        }
    }

    private class Holder(val avatar: TextView, val name: TextView, val time: TextView, val prev: TextView, val badge: TextView)

    private inner class ChatsAdapter : BaseAdapter() {
        override fun getCount() = chats.size
        override fun getItem(position: Int): Any = chats[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val row: View
            val h: Holder
            if (convertView?.tag is Holder) { row = convertView; h = convertView.tag as Holder }
            else {
                val r = LinearLayout(this@ChatsActivity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(dp(14), dp(10), dp(14), dp(10))
                    layoutParams = AbsListView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                }
                val av = TextView(this@ChatsActivity).apply {
                    gravity = Gravity.CENTER; textSize = 19f; setTextColor(0xFFFFFFFF.toInt()); setTypeface(null, Typeface.BOLD)
                }
                r.addView(av, LinearLayout.LayoutParams(dp(48), dp(48)).apply { marginEnd = dp(12) })
                val mid = LinearLayout(this@ChatsActivity).apply { orientation = LinearLayout.VERTICAL }
                val l1 = LinearLayout(this@ChatsActivity).apply { orientation = LinearLayout.HORIZONTAL }
                val name = TextView(this@ChatsActivity).apply {
                    textSize = 16.5f; setTypeface(null, Typeface.BOLD); setSingleLine(true); ellipsize = TextUtils.TruncateAt.END
                    setTextColor(color(R.color.text_plain))
                }
                val time = TextView(this@ChatsActivity).apply { textSize = 12f }
                l1.addView(name, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                l1.addView(time)
                val l2 = LinearLayout(this@ChatsActivity).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
                val prev = TextView(this@ChatsActivity).apply {
                    textSize = 14f; setSingleLine(true); ellipsize = TextUtils.TruncateAt.END; setTextColor(color(R.color.text_meta))
                }
                val badge = TextView(this@ChatsActivity).apply {
                    textSize = 12f; setTypeface(null, Typeface.BOLD); gravity = Gravity.CENTER
                    setTextColor(0xFFFFFFFF.toInt()); minWidth = dp(22); setPadding(dp(6), dp(1), dp(6), dp(1))
                    background = GradientDrawable().apply { cornerRadius = dp(11).toFloat(); setColor(color(R.color.accent)) }
                }
                l2.addView(prev, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                l2.addView(badge)
                mid.addView(l1); mid.addView(l2)
                r.addView(mid, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                h = Holder(av, name, time, prev, badge)
                r.tag = h
                row = r
            }
            val c = chats[position]
            val t = chatTitle(c)
            h.avatar.text = when (c.second) { "public" -> "📢"; "group" -> "👥"; "own" -> "📣"; "follow" -> "📻"; else -> t.take(1).uppercase() }
            h.avatar.background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(if (c.second == "public") color(R.color.accent) else avatarColor(t))
            }
            h.name.text = t
            val last = session.lastOf(c.first)
            val unread = session.unreadBy[c.first] ?: 0
            h.time.text = last?.let { whenText(it.ts) } ?: ""
            h.time.setTextColor(color(if (unread > 0) R.color.accent else R.color.text_meta))
            h.prev.text = preview(c, last)
            h.badge.text = unread.toString()
            h.badge.visibility = if (unread > 0) View.VISIBLE else View.GONE
            return row
        }
    }

    // -- new chat: by number -------------------------------------------------------------------

    private fun newChat() {
        val n = node ?: return
        if (n.locked) { unlock(n); return }
        val num = field("e.g. 512 384 9076").apply { inputType = InputType.TYPE_CLASS_PHONE }
        val st = label("Type their TextCast number. Your phone asks nearby devices who has it.")
        val name = field("Save as").apply { visibility = View.GONE }
        val box = column()
        box.addView(num); box.addView(st); box.addView(name)
        var found: String? = null
        val d = AlertDialog.Builder(this).setTitle(R.string.new_chat).setView(box)
            .setPositiveButton("Find", null).setNeutralButton("More…", null)
            .setNegativeButton(android.R.string.cancel, null).create()
        d.setOnShowListener {
            val ok: Button = d.getButton(AlertDialog.BUTTON_POSITIVE)
            ok.setOnClickListener {
                val f = found
                if (f != null) {
                    try {
                        val ch = n.addFound(f, name.text.toString().trim().ifEmpty { null })
                        d.dismiss()
                        refresh()
                        session.openConversation(this, ch)
                    } catch (e: Exception) { st.text = e.text() }
                    return@setOnClickListener
                }
                try {
                    val r = n.lookup(num.text.toString())
                    if (r.status == "known") { d.dismiss(); session.openConversation(this, "@" + r.handle); return@setOnClickListener }
                    st.text = "Looking for ${Numbers.format(r.number)} nearby… (up to 10 s)"
                    ok.isEnabled = false
                    lookupHandler = { res ->
                        if (res.number == r.number) {
                            ok.isEnabled = true
                            when (res.status) {
                                "found" -> {
                                    found = res.number
                                    st.text = "Found ${res.nick} nearby. Save them as:"
                                    name.setText(res.nick)
                                    name.visibility = View.VISIBLE
                                    num.isEnabled = false
                                    ok.text = "Add and chat"
                                }
                                "conflict" -> {
                                    st.text = "⚠ Two different devices answered for this number (${res.nick}). " +
                                        "Someone may be pretending: add the person with their identity string instead (More…)."
                                }
                                else -> {
                                    st.text = "Nobody answered for ${Numbers.format(res.number)}. Check the number. " +
                                        "They need TextCast open with the radio plugged in, within range."
                                }
                            }
                        }
                    }
                } catch (e: NumberException) {
                    st.text = e.text()
                } catch (e: KeyException) {
                    st.text = if (n.radio == null) "Plug the radio in first: the search goes over the radio." else e.text()
                }
            }
            d.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener { d.dismiss(); moreWays() }
        }
        d.setOnDismissListener { lookupHandler = null }
        d.show()
    }

    private fun keysUi(n: Node) = KeysUi(this, n, { refresh() }, { ch -> session.openConversation(this, ch) })

    private fun moreWays() {
        val n = node ?: return
        val items = arrayOf("Paste an identity string or a group invite", "New group", "Groups and channels")
        AlertDialog.Builder(this).setTitle(R.string.new_chat).setItems(items) { _, w ->
            when (w) {
                0 -> keysUi(n).addKey()
                1 -> keysUi(n).newGroup()
                else -> keysUi(n).showKeys()
            }
        }.show()
    }

    private fun showNumber(n: Node) {
        val number = Numbers.format(n.number)
        val msg = "Your TextCast number\n\n$number\n\nPeople nearby add you with it (+ on their phone)." +
            if (n.keys.findable) "" else "\n\nFinding you by number is switched off."
        AlertDialog.Builder(this).setTitle(n.nick ?: "Me").setMessage(msg)
            .setPositiveButton("Copy") { _, _ -> copyText("TextCast number", number) }
            .setNeutralButton("Identity string") { _, _ -> keysUi(n).showIdentity() }
            .setNegativeButton("Close", null).show()
    }

    // -- menu (settings) ------------------------------------------------------------------------

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, M_NUMBER, 0, R.string.menu_number)
        menu.add(0, M_FINDABLE, 1, R.string.menu_findable).isCheckable = true
        menu.add(0, M_GROUPS, 2, R.string.menu_groups)
        menu.add(0, M_RADIO, 3, R.string.radio_settings)
        menu.add(0, M_LOG, 4, R.string.menu_log)
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
        menu.findItem(M_FINDABLE).isChecked = n.keys.findable
        for (id in listOf(M_NUMBER, M_FINDABLE, M_GROUPS, M_EXPORT)) menu.findItem(id).isEnabled = !n.locked
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        val n = node ?: return true
        when (item.itemId) {
            M_NUMBER -> showNumber(n)
            M_FINDABLE -> try { n.setFindable(!n.keys.findable); invalidateOptionsMenu() } catch (e: Exception) { toast(e.text()) }
            M_GROUPS -> keysUi(n).showKeys()
            M_RADIO -> startActivity(Intent(this, RadioSettingsActivity::class.java))
            M_LOG -> showLog()
            M_LOCK -> if (n.locked) unlock(n) else { n.lockIdentity(); refresh() }
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

    private fun showLog() {
        val n = node ?: return
        val lines = session.messages.filter { it.direction != "sys" }.takeLast(200).reversed().joinToString("\n\n") { m ->
            val where = when (m.channel) { "other" -> "not for you"; "public" -> "public"; else -> n.channelTitle(m.channel) }
            val sig = if (m.rssi != null) " · ${m.rssi} dBm" + (m.snr?.let { " · SNR $it" } ?: "") else ""
            "${whenText(m.ts)} ${timeFmt.format(Date((m.ts * 1000).toLong()))} · ${if (m.direction == "tx") "sent" else "heard"} · " +
                "${m.kind} · $where$sig · ${m.size} B\n${m.sender?.let { "$it: " } ?: ""}${m.text}"
        }.ifEmpty { "Nothing heard yet." }
        info(getString(R.string.menu_log), lines)
    }

    private fun unlock(n: Node) {
        val pw = field("Passphrase", password = true)
        form("Unlock", null, listOf(pw), "Unlock") {
            val p = pw.text.toString()
            bg({ n.unlock(p) }) { r ->
                r.onSuccess {
                    session.messages.clear(); session.messages.addAll(n.history())
                    refresh()
                }.onFailure { e -> toast(if (e is WrongPassphrase) "Wrong passphrase" else e.text()) }
            }
            null
        }
    }

    private var exportBlob: ByteArray? = null

    private fun exportIdentity(n: Node) {
        askNewPassphrase("Back up identity",
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
                toast("Identity backed up")
            } catch (e: Exception) { toast("Backup failed: ${e.text()}") }
            exportBlob = null
        }
    }

    companion object {
        val CHAT_TYPES = setOf("public", "direct", "group", "own", "follow")
        private const val M_NUMBER = 1
        private const val M_FINDABLE = 2
        private const val M_GROUPS = 3
        private const val M_RADIO = 4
        private const val M_LOG = 5
        private const val M_LOCK = 6
        private const val M_PASSWD = 7
        private const val M_EXPORT = 8
        private const val M_LOGOUT = 9
        private const val REQ_EXPORT = 20
        private val AVATAR = intArrayOf(0xFFC0392B.toInt(), 0xFFD35400.toInt(), 0xFFB7950B.toInt(), 0xFF27AE60.toInt(),
            0xFF16A085.toInt(), 0xFF2980B9.toInt(), 0xFF8E44AD.toInt(), 0xFF2C3E50.toInt(), 0xFFA0522D.toInt(), 0xFF5D6D7E.toInt())

        /** The same name always gets the same colour (as in the web app). */
        fun avatarColor(name: String): Int {
            var h = 0L
            for (ch in name) h = (h * 31 + ch.code) and 0xFFFFFFFFL
            return AVATAR[(h % AVATAR.size).toInt()]
        }
    }
}
