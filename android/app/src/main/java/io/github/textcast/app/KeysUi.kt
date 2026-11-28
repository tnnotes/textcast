package io.github.textcast.app

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Typeface
import android.widget.TextView
import io.github.textcast.core.KeyException
import io.github.textcast.core.Node

/** Dialogs for streams, groups, people (contacts) and the own identity. */
class KeysUi(private val a: Activity, private val node: Node, private val changed: () -> Unit,
             private val openChat: (String) -> Unit = {}) {

    fun showKeys() {
        val items = node.keys.listing()
        val names = items.map { (label, type, desc) ->
            when (type) { "group" -> "👥 "; "own" -> "📣 "; else -> "👁 " } + "$label — $desc"
        } + listOf("+ New group", "+ New stream (you post, followers read)", "+ Add a key string…")
        AlertDialog.Builder(a).setTitle("Keys and groups").setItems(names.toTypedArray()) { _, which ->
            when (which - items.size) {
                0 -> newGroup()
                1 -> newStream()
                2 -> addKey()
                else -> keyActions(items[which].first, items[which].second)
            }
        }.setNegativeButton("Close", null).show()
    }

    private fun shareString(title: String, s: String, note: String) {
        val tv = TextView(a).apply {
            text = s
            typeface = Typeface.MONOSPACE
            textSize = 13f
            setTextIsSelectable(true)
            setPadding(a.dp(20), a.dp(12), a.dp(20), a.dp(4))
        }
        val box = a.column().apply { addView(tv); addView(a.label(note)) }
        AlertDialog.Builder(a).setTitle(title).setView(box)
            .setPositiveButton("Share…") { _, _ -> a.shareText(title, s) }
            .setNeutralButton("Copy") { _, _ -> a.copyText(title, s) }
            .setNegativeButton("Close", null).show()
    }

    fun keyActions(label: String, type: String) {
        val actions = mutableListOf("Share the key string")
        if (type == "group") { actions.add("Announce me to the group"); actions.add("Add a member by handle…") }
        actions.add("Delete")
        AlertDialog.Builder(a).setTitle(label).setItems(actions.toTypedArray()) { _, w ->
            when (actions[w]) {
                "Share the key string" -> shareString(label, node.keyShare(label), when (type) {
                    "group" -> "Anyone with this string can read and post in the group. Share it only with members."
                    "own" -> "Followers can read your stream. The string is signed with your identity."
                    else -> "This string lets others follow the same stream."
                })
                "Announce me to the group" -> try { node.announce(label); a.toast("Announcing yourself to $label") }
                    catch (e: Exception) { a.toast(e.text()) }
                "Add a member by handle…" -> {
                    val f = a.field("Handle, e.g. Lan#P2MD")
                    a.form("Add member to $label", "Members announce themselves when they join; add by hand only if needed.", listOf(f)) {
                        val h = f.text.toString().trim()
                        if (h.isEmpty()) "type a handle" else try { node.keyMember(label, h); changed(); null } catch (e: KeyException) { e.text() }
                    }
                }
                "Delete" -> a.confirm("Delete $label", "Delete this key from your identity? You can add it again only with its key string.", "Delete") {
                    node.keyDelete(label); changed()
                }
            }
        }.show()
    }

    fun newGroup() {
        val f = a.field("Group name (one word)")
        a.form("New group", "Members share one key; anyone holding the group string can read and post.", listOf(f), "Create") {
            try {
                val share = node.keyNewGroup(f.text.toString().trim())
                changed()
                shareString("Group created", share, "Send this string to the members (in person, chat or QR).")
                null
            } catch (e: KeyException) { e.text() }
        }
    }

    private fun newStream() {
        val f = a.field("Stream name (one word)")
        a.form("New stream", "You post, followers read. Your posts are sealed; followers need the stream string.", listOf(f), "Create") {
            try {
                val share = node.keyNewStream(f.text.toString().trim())
                changed()
                shareString("Stream created", share, "Give this string to your followers.")
                null
            } catch (e: KeyException) { e.text() }
        }
    }

    fun addKey() {
        val clip = a.clipboardText()?.trim()?.takeIf { s -> listOf("lts", "ltg", "lid").any { s.startsWith(it) } } ?: ""
        val s = a.field("Paste lts3:…, ltg2:… or lid1:…", multiline = true, value = clip)
        val name = a.field("Name (optional)")
        a.form("Add a key string", "A group string joins the group, a stream string follows a stream, an identity string saves a contact.",
            listOf(s, name), "Add") {
            try {
                val (label, type) = node.keyAdd(s.text.toString(), name.text.toString().trim().ifEmpty { null })
                changed()
                if (type == "contact") {
                    val handle = node.keys.directContacts().firstOrNull { it.second == label }?.first
                    if (handle != null) a.confirm("$label added", "Send $label a private message now? They need your identity string too.",
                        "Message $label") { openChat("@$handle") }
                } else a.toast(when (type) { "group" -> "Joined group $label"; else -> "Following $label" })
                null
            } catch (e: KeyException) { e.text() }
        }
    }

    fun showIdentity() {
        val lid = try { node.keys.lid() } catch (e: KeyException) { a.toast(e.text()); return }
        shareString(node.handle ?: "Identity", lid,
            "Fingerprint ${node.keys.fingerprint()}\nCompare the fingerprint in person before trusting a key.")
    }

    /** People you can message privately: exchange identity strings, then tap a name. */
    fun showContacts() {
        val me = node.handle
        val cs = node.keys.contacts().filter { it.second != me }
        val names = cs.map { (n, h, fp) -> "👤 $n\n$h · $fp" } + listOf("+ Add a person (paste their lid1: string)", "Share my identity string")
        AlertDialog.Builder(a).setTitle("People").setItems(names.toTypedArray()) { _, w ->
            when (w - cs.size) {
                0 -> addKey()
                1 -> showIdentity()
                else -> {
                    val c = cs[w]
                    AlertDialog.Builder(a).setTitle(c.first).setItems(arrayOf("Send a private message", "Delete")) { _, k ->
                        if (k == 0) openChat("@" + c.second)
                        else a.confirm("Delete ${c.first}", "Remove this person? You can add them again with their identity string.", "Delete") {
                            node.contactDel(c.first); changed()
                        }
                    }.show()
                }
            }
        }.setNegativeButton("Close", null).show()
    }
}
