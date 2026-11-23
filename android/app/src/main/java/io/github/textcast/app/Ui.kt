package io.github.textcast.app

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.text.InputType
import android.view.View
import android.view.WindowManager
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

fun Context.dp(v: Int): Int = (v * resources.displayMetrics.density + 0.5f).toInt()

fun Context.toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

/** Run [work] off the main thread, then [done] on it (only if the activity is still alive). */
fun <T> Activity.bg(work: () -> T, done: (Result<T>) -> Unit) {
    Thread {
        val r = runCatching(work)
        runOnUiThread { if (!isFinishing) done(r) }
    }.start()
}

fun Throwable.text(): String = message ?: javaClass.simpleName

fun Context.copyText(label: String, text: String) {
    val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    cm.setPrimaryClip(ClipData.newPlainText(label, text))
    toast("Copied")
}

fun Context.clipboardText(): String? {
    val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    return try { cm.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString() } catch (e: Exception) { null }
}

fun Activity.shareText(subject: String, text: String) {
    val i = Intent(Intent.ACTION_SEND).setType("text/plain")
        .putExtra(Intent.EXTRA_SUBJECT, subject).putExtra(Intent.EXTRA_TEXT, text)
    startActivity(Intent.createChooser(i, subject))
}

fun Activity.column(): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    setPadding(dp(20), dp(8), dp(20), 0)
}

fun Activity.field(hint: String, password: Boolean = false, multiline: Boolean = false, value: String = ""): EditText =
    EditText(this).apply {
        this.hint = hint
        setText(value)
        inputType = when {
            password -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            multiline -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            else -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        }
        if (multiline) { minLines = 3; maxLines = 6 } else setSingleLine(true)
    }

fun Activity.label(text: String, small: Boolean = true): TextView = TextView(this).apply {
    this.text = text
    if (small) textSize = 13f
    setPadding(0, dp(6), 0, dp(2))
}

fun Activity.confirm(title: String, message: String, yes: String = "OK", action: () -> Unit) {
    AlertDialog.Builder(this).setTitle(title).setMessage(message)
        .setPositiveButton(yes) { _, _ -> action() }
        .setNegativeButton(android.R.string.cancel, null).show()
}

fun Activity.info(title: String, message: String) {
    AlertDialog.Builder(this).setTitle(title).setMessage(message).setPositiveButton(android.R.string.ok, null).show()
}

/**
 * Dialog with input fields; [onOk] returns an error text to keep the dialog
 * open, or null to close it.
 */
fun Activity.form(title: String, message: String?, fields: List<View>, ok: String = "OK",
                  onOk: () -> String?): AlertDialog {
    val box = column()
    if (message != null) box.addView(label(message, small = false))
    fields.forEach { box.addView(it) }
    val err = label("").apply { setTextColor(0xFFC62828.toInt()); visibility = View.GONE }
    box.addView(err)
    val d = AlertDialog.Builder(this).setTitle(title).setView(box)
        .setPositiveButton(ok, null).setNegativeButton(android.R.string.cancel, null).create()
    d.setOnShowListener {
        d.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val e = onOk()
            if (e == null) d.dismiss() else { err.text = e; err.visibility = View.VISIBLE }
        }
    }
    d.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE)
    d.show()
    return d
}

/** Ask for a new passphrase twice; [onOk] gets it (null = lab mode if [allowNone]). */
fun Activity.askNewPassphrase(title: String, message: String?, onOk: (String) -> Unit) {
    val a = field("New passphrase (8+ characters)", password = true)
    val b = field("Again", password = true)
    form(title, message, listOf(a, b)) {
        val p = a.text.toString()
        when {
            p.length < 8 -> "at least 8 characters"
            p != b.text.toString() -> "the two passphrases differ"
            else -> { onOk(p); null }
        }
    }
}

fun Activity.checkbox(text: String, checked: Boolean = false): CheckBox =
    CheckBox(this).apply { this.text = text; isChecked = checked }

@Suppress("DEPRECATION")
fun Context.color(id: Int): Int = if (android.os.Build.VERSION.SDK_INT >= 23) getColor(id) else resources.getColor(id)
