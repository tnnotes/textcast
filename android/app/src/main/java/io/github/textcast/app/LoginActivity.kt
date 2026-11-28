package io.github.textcast.app

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import io.github.textcast.R
import io.github.textcast.core.Ids
import io.github.textcast.core.Json
import io.github.textcast.core.KeyException
import io.github.textcast.core.LockedException
import io.github.textcast.core.Vault
import io.github.textcast.core.WrongPassphrase

/**
 * Login: identity, passphrase, radio. Kept as clean as the desktop login
 * panel; the radio module and settings live in Settings.
 */
class LoginActivity : Activity() {
    private lateinit var identity: Spinner
    private lateinit var nick: EditText
    private lateinit var pass: EditText
    private lateinit var pass2: EditText
    private lateinit var lab: CheckBox
    private lateinit var device: Spinner
    private lateinit var status: TextView
    private lateinit var start: Button

    private var identities: List<Session.Identity> = emptyList()
    private var devices: List<UsbChoice> = emptyList()
    private var busy = false

    private val newIndex get() = identities.size          // "+ New identity"
    private val importIndex get() = identities.size + 1   // "Import from file…"

    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            when (intent.action) {
                ACTION_USB_PERMISSION -> {
                    if (intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)) doStart()
                    else { setBusy(false); showStatus("USB permission was refused", error = true) }
                }
                UsbManager.ACTION_USB_DEVICE_ATTACHED, UsbManager.ACTION_USB_DEVICE_DETACHED -> refreshDevices()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (session.node != null) { session.reattach(); session.openChat(this); finish(); return }
        setContentView(R.layout.activity_login)
        identity = findViewById(R.id.identity)
        nick = findViewById(R.id.nick)
        pass = findViewById(R.id.pass)
        pass2 = findViewById(R.id.pass2)
        lab = findViewById(R.id.lab)
        device = findViewById(R.id.device)
        status = findViewById(R.id.status)
        start = findViewById(R.id.start)

        identity.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) = identityChanged(pos)
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }
        lab.setOnCheckedChangeListener { _, on -> pass.isEnabled = !on; pass2.isEnabled = !on }
        findViewById<Button>(R.id.refresh).setOnClickListener { refreshDevices() }
        findViewById<Button>(R.id.settings).setOnClickListener {
            startActivity(Intent(this, RadioSettingsActivity::class.java))
        }
        start.setOnClickListener { onStartClicked() }
        val go = TextView.OnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_DONE || action == EditorInfo.IME_ACTION_GO) { onStartClicked(); true } else false
        }
        pass.setOnEditorActionListener(go); pass2.setOnEditorActionListener(go)

        val f = IntentFilter(ACTION_USB_PERMISSION)
        f.addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
        f.addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(usbReceiver, f, Context.RECEIVER_NOT_EXPORTED)
        else registerReceiver(usbReceiver, f)

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"), 1)

        refreshIdentities()
        refreshDevices()
    }

    override fun onDestroy() {
        try { unregisterReceiver(usbReceiver) } catch (e: Exception) { }
        super.onDestroy()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        refreshDevices()                      // started again by plugging in a module
    }

    override fun onResume() {
        super.onResume()
        if (session.node != null) { session.reattach(); session.openChat(this); finish() }
    }

    // -- lists -------------------------------------------------------------------------

    private fun refreshIdentities(select: String? = null) {
        identities = session.identities()
        val names = identities.map { if (it.protected) it.nick else "${it.nick}  (no passphrase)" } +
            listOf("+ New identity", "Import from file…")
        identity.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, names)
        val last = select ?: session.config()["ui"].let { (it as? Map<*, *>)?.get("nick") as? String }
        val idx = identities.indexOfFirst { it.nick.equals(last, ignoreCase = true) }
        identity.setSelection(if (idx >= 0) idx else if (identities.isEmpty()) newIndex else 0)
    }

    private fun refreshDevices() {
        devices = try { session.usbChoices() } catch (e: Exception) { emptyList() }
        val names = devices.map { it.label } + listOf("Connect later (plug it in anytime)")
        device.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, names)
        val last = (session.config()["ui"] as? Map<*, *>)?.get("port") as? String
        val idx = devices.indexOfFirst { it.key == last }
        device.setSelection(if (idx >= 0) idx else 0)
    }

    private fun identityChanged(pos: Int) {
        if (pos == importIndex) { pickImport(); return }
        val isNew = pos == newIndex
        nick.visibility = if (isNew) View.VISIBLE else View.GONE
        lab.visibility = if (isNew) View.VISIBLE else View.GONE
        val oldPlain = !isNew && pos < identities.size && !identities[pos].protected
        pass2.visibility = if (isNew || oldPlain) View.VISIBLE else View.GONE
        pass.hint = when {
            isNew -> "Choose a passphrase (8+ characters)"
            oldPlain -> "New passphrase to protect it (optional)"
            else -> getString(R.string.passphrase)
        }
        if (!isNew) lab.isChecked = false
        status.visibility = View.GONE
    }

    // -- start ---------------------------------------------------------------------------

    private class Plan(val nick: String, val file: java.io.File, val passphrase: String?, val usb: UsbChoice?,
                       val protectFrom: java.io.File? = null)

    private var plan: Plan? = null

    private fun showStatus(text: String, error: Boolean = false) {
        status.text = text
        status.setTextColor(color(if (error) R.color.text_error else R.color.text_meta))
        status.visibility = View.VISIBLE
    }

    private fun setBusy(b: Boolean) {
        busy = b
        start.isEnabled = !b
        identity.isEnabled = !b
        device.isEnabled = !b
    }

    private fun onStartClicked() {
        if (busy) return
        val pos = identity.selectedItemPosition
        val p1 = pass.text.toString()
        val p2 = pass2.text.toString()
        val usb = devices.getOrNull(device.selectedItemPosition)
        val p: Plan = try {
            if (pos == newIndex) {
                val n = nick.text.toString().trim()
                Ids.checkNick(n.ifEmpty { null } ?: throw KeyException("type a nick"))
                if (identities.any { it.nick.equals(n, true) }) throw KeyException("an identity called '$n' already exists")
                if (lab.isChecked) Plan(n, session.identityFile(n, false), null, usb)
                else {
                    if (p1.length < 8) throw KeyException("the passphrase needs at least 8 characters")
                    if (p1 != p2) throw KeyException("the two passphrases differ")
                    Plan(n, session.identityFile(n, true), p1, usb)
                }
            } else {
                val id = identities.getOrNull(pos) ?: throw KeyException("choose an identity")
                if (id.protected) {
                    if (p1.isEmpty()) throw KeyException("type your passphrase")
                    Plan(id.nick, id.file, p1, usb)
                } else if (p1.isNotEmpty()) {           // protect an old plain identity now
                    if (p1.length < 8) throw KeyException("the passphrase needs at least 8 characters")
                    if (p1 != p2) throw KeyException("the two passphrases differ")
                    Plan(id.nick, session.identityFile(id.nick, true), p1, usb, protectFrom = id.file)
                } else Plan(id.nick, id.file, null, usb)
            }
        } catch (e: KeyException) {
            showStatus(e.text(), error = true); return
        }
        plan = p
        setBusy(true)
        if (usb != null && !session.usbManager().hasPermission(usb.device)) {
            showStatus("Allow TextCast to use the USB device…")
            val i = Intent(ACTION_USB_PERMISSION).setPackage(packageName)
            val flags = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
            session.usbManager().requestPermission(usb.device, PendingIntent.getBroadcast(this, 0, i, flags))
            return
        }
        doStart()
    }

    private fun doStart() {
        val p = plan ?: return
        showStatus(if (p.usb != null) "Unlocking and checking the radio (a few seconds)…" else "Unlocking…")
        bg({
            if (p.protectFrom != null) protectPlain(p.protectFrom, p.file, p.passphrase!!)
            session.connect(p.nick, p.file, p.passphrase, p.usb)
        }) { r ->
            setBusy(false)
            r.onSuccess {
                val cfg = session.config()
                @Suppress("UNCHECKED_CAST")
                (cfg["ui"] as MutableMap<String, Any?>).apply { put("nick", p.nick); p.usb?.key?.let { put("port", it) } }
                session.saveConfig(cfg)
                pass.setText(""); pass2.setText("")
                session.openChat(this)
                finish()
            }.onFailure { e ->
                val msg = when (e) {
                    is WrongPassphrase -> "Wrong passphrase"
                    is LockedException -> "Type your passphrase"
                    else -> e.text()
                }
                showStatus(msg, error = true)
            }
        }
    }

    /** Copy an old plain identity into a passphrase-protected file (the plain one is kept as .bak). */
    private fun protectPlain(from: java.io.File, to: java.io.File, passphrase: String) {
        val data = Json.parseObject(from.readText())
        to.writeBytes(Vault.create(passphrase).seal(data))
        from.renameTo(java.io.File(from.path + ".bak"))
    }

    // -- import an identity (.ltid / .lkey / lab .json) -----------------------------------

    private fun pickImport() {
        identity.setSelection(if (identities.isEmpty()) newIndex else 0)
        val i = Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
        startActivityForResult(i, REQ_IMPORT)
    }

    @Deprecated("Activity API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_IMPORT || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        val blob = try { contentResolver.openInputStream(uri)!!.use { it.readBytes() } } catch (e: Exception) {
            toast("Cannot read the file: ${e.text()}"); return
        }
        if (!Vault.isProtected(blob)) {
            val obj = try { Json.parseObject(String(blob, Charsets.UTF_8)) } catch (e: Exception) { null }
            val me = obj?.get("me") as? String
            if (obj == null || me == null || obj["id_seed"] == null) { toast("Not a TextCast identity file"); return }
            saveImported(me, blob, protected = false)
            return
        }
        val pw = field("Passphrase of the file", password = true)
        form("Import identity", "Move, don't clone: stop using this identity on the other device.", listOf(pw), "Import") {
            val passphrase = pw.text.toString()
            showStatus("Opening the identity file…")
            bg({ Vault.open(blob, passphrase).first }) { r ->
                r.onSuccess { d ->
                    val me = d["me"] as? String
                    if (me == null) showStatus("The file has no nick", error = true)
                    else { saveImported(me, blob, protected = true); status.visibility = View.GONE }
                }.onFailure { e -> showStatus(if (e is WrongPassphrase) "Wrong passphrase for that file" else e.text(), error = true) }
            }
            null
        }
    }

    private fun saveImported(me: String, blob: ByteArray, protected: Boolean) {
        if (identities.any { it.nick.equals(me, true) }) { toast("An identity called '$me' already exists on this phone"); return }
        session.identityFile(me, protected).writeBytes(blob)
        refreshIdentities(select = me)
        toast("Imported $me")
    }

    companion object {
        const val ACTION_USB_PERMISSION = "io.github.textcast.USB_PERMISSION"
        private const val REQ_IMPORT = 10
    }
}
