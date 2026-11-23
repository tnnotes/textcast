package io.github.textcast.app

import android.app.Application
import android.content.Context
import android.content.Intent
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import com.hoho.android.usbserial.driver.CdcAcmSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import io.github.textcast.core.AirProfile
import io.github.textcast.core.ConfigError
import io.github.textcast.core.Hardware
import io.github.textcast.core.HardwareError
import io.github.textcast.core.HardwareProfile
import io.github.textcast.core.Msg
import io.github.textcast.core.Node
import io.github.textcast.core.Protocol
import io.github.textcast.core.SerialLink
import io.github.textcast.core.Settings
import io.github.textcast.core.asMap
import io.github.textcast.core.makeRadio
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

class TextCastApp : Application() {
    lateinit var session: Session
        private set

    override fun onCreate() {
        super.onCreate()
        session = Session(this)
    }
}

val Context.session: Session get() = (applicationContext as TextCastApp).session

/** USB serial port as the core's SerialLink. */
class UsbLink(private val port: UsbSerialPort, private val onClosed: () -> Unit = {}) : SerialLink {
    override fun read(buf: ByteArray, timeoutMs: Int): Int = port.read(buf, timeoutMs)
    override fun write(data: ByteArray) = port.write(data, 2000)
    override fun close() {
        try { port.close() } catch (e: Exception) { }
        onClosed()
    }
}

/** A USB device that can be opened as a serial port. */
class UsbChoice(val driver: UsbSerialDriver) {
    val device: UsbDevice get() = driver.device
    val key: String get() = String.format("%04X:%04X", device.vendorId, device.productId)
    val label: String
        get() {
            val name = try { device.productName } catch (e: Exception) { null }
            val chip = driver.javaClass.simpleName.removeSuffix("SerialDriver")
            return listOfNotNull(name, "$chip $key").joinToString(" · ")
        }
}

/**
 * Everything that lives while the app runs: settings, the node, received
 * messages. Activities come and go; the session (and the radio) stays.
 */
class Session(val app: Application) {
    interface Listener {
        fun onMessage(m: Msg) {}
        fun onUpdate(m: Msg) {}
        fun onLog(text: String) {}
        fun onState() {}
    }

    val main = Handler(Looper.getMainLooper())
    val configFile = File(app.filesDir, "config.json")
    val hardwareDir = File(app.filesDir, "hardware").apply { mkdirs() }
    private val listeners = CopyOnWriteArrayList<Listener>()
    val messages = ArrayList<Msg>()          // main thread only
    var node: Node? = null; private set
    var deviceLabel: String? = null; private set
    var hw: HardwareProfile = Hardware.load(null); private set
    var hwId: String = Hardware.DEFAULT; private set
    var startProblems: List<String> = emptyList(); private set
    var visible = false
    var unread = 0

    init {
        loadSettings()
        // the module was unplugged: stop the radio
        val detach = object : android.content.BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                val dev = deviceName ?: return
                if (usbManager().deviceList.values.none { it.deviceName == dev }) radioLost()
            }
        }
        val f = android.content.IntentFilter(UsbManager.ACTION_USB_DEVICE_DETACHED)
        if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(detach, f, Context.RECEIVER_NOT_EXPORTED)
        else app.registerReceiver(detach, f)
    }

    private var deviceName: String? = null

    fun addListener(l: Listener) = listeners.add(l)
    fun removeListener(l: Listener) = listeners.remove(l)

    // -- settings ---------------------------------------------------------------------

    fun config(): MutableMap<String, Any?> = try { Settings.load(configFile) } catch (e: ConfigError) { Settings.defaults() }

    fun saveConfig(cfg: Map<String, Any?>) = Settings.save(cfg, configFile)

    /** Apply the saved module and radio profile (as the desktop app does at start). */
    fun loadSettings() {
        val cfg = config()
        val id = cfg["hardware"].asMap()["type"] as? String ?: Hardware.DEFAULT
        activateHardware(id)
        val p = try { Settings.buildProfile(cfg["radio"].asMap()) } catch (e: ConfigError) { Settings.buildProfile(emptyMap()) }
        Protocol.applyProfile(p)
    }

    fun loadHardware(id: String): HardwareProfile = try { Hardware.load(id) } catch (e: HardwareError) { Hardware.load(null) }

    fun activateHardware(id: String) {
        hw = loadHardware(id)
        hwId = if (hw.name == Hardware.DEFAULT && id != Hardware.DEFAULT && !File(id).exists()) Hardware.DEFAULT else id
        Protocol.setHardwareLimits(hw.maxPayload.ifEmpty { mapOf("*" to 255) }, hw.firmwareOverhead)
        val p = Protocol.profile.copy()
        Protocol.applyProfile(p)
    }

    fun hardwareChoices(): List<Pair<String, String>> =
        Hardware.available(hardwareDir).filterNot { it.second.startsWith("invalid") }

    fun logLimits(): Pair<Int, Double> {
        val log = config()["log"].asMap()
        return ((log["max_rows"] as? Number)?.toInt() ?: 20000) to ((log["max_days"] as? Number)?.toDouble() ?: 30.0)
    }

    // -- identities ---------------------------------------------------------------------

    class Identity(val nick: String, val file: File) {
        val protected: Boolean get() = file.name.endsWith(".lkey")
    }

    fun identities(): List<Identity> {
        val files = app.filesDir.listFiles { f -> f.name.startsWith("keys-") && (f.name.endsWith(".lkey") || f.name.endsWith(".json")) }
            ?: return emptyList()
        val byNick = LinkedHashMap<String, Identity>()
        for (f in files.sortedBy { it.name.lowercase() }) {
            val nick = f.name.removePrefix("keys-").substringBeforeLast('.')
            val old = byNick[nick.lowercase()]
            if (old == null || f.name.endsWith(".lkey")) byNick[nick.lowercase()] = Identity(nick, f)
        }
        return byNick.values.toList()
    }

    fun identityFile(nick: String, protected: Boolean) = File(app.filesDir, "keys-$nick.${if (protected) "lkey" else "json"}")
    fun logFile(nick: String) = File(app.filesDir, "log-$nick.jsonl")

    // -- USB ------------------------------------------------------------------------------

    fun usbManager() = app.getSystemService(Context.USB_SERVICE) as UsbManager

    fun usbChoices(): List<UsbChoice> {
        val mgr = usbManager()
        val known = UsbSerialProber.getDefaultProber().findAllDrivers(mgr)
        val seen = known.map { it.device.deviceName }.toSet()
        // unknown chips: try them as standard USB serial (CDC ACM)
        val others = mgr.deviceList.values.filter { it.deviceName !in seen }.map { CdcAcmSerialDriver(it) }
        return (known + others).map { UsbChoice(it) }
    }

    // -- connecting -----------------------------------------------------------------------

    /**
     * Open the identity and the radio. Blocks (scrypt + module check): call from
     * a background thread. [usb] null = offline (history and keys only).
     */
    fun connect(nick: String, file: File, passphrase: String?, usb: UsbChoice?) {
        disconnect()
        val (rows, days) = logLimits()
        val n = Node(file, nick, passphrase, logFile(nick), logMaxRows = rows, logMaxDays = days)
        n.onMsg = { m -> main.post { addMessage(m); listeners.forEach { it.onMessage(m) }; notifyIfHidden(m) } }
        n.onUpdate = { m -> main.post { listeners.forEach { it.onUpdate(m) } } }
        n.onLog = { s -> main.post { addSystem(s); listeners.forEach { it.onLog(s) } } }
        var problems = emptyList<String>()
        if (usb != null) {
            val conn = usbManager().openDevice(usb.device)
                ?: run { n.stop(); throw IllegalStateException("no permission to open ${usb.label}") }
            val port = usb.driver.ports[0]
            try {
                port.open(conn)
                port.setParameters(hw.baud, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
                try { port.setDTR(true); port.setRTS(true) } catch (e: Exception) { }
            } catch (e: Exception) {
                try { port.close() } catch (x: Exception) { }
                n.stop()
                throw IllegalStateException("cannot open ${usb.label}: ${e.message}")
            }
            val link = UsbLink(port) { try { conn.close() } catch (e: Exception) { } }
            try {
                problems = n.start(makeRadio(hw, link, hw.baud))
            } catch (e: Exception) {
                n.stop()
                throw e
            }
        }
        val hist = n.history()
        main.post {
            node = n
            deviceLabel = usb?.label
            deviceName = usb?.device?.deviceName
            startProblems = problems
            messages.clear(); messages.addAll(hist)
            unread = 0
            if (usb != null) RadioService.start(app)
            listeners.forEach { it.onState() }
        }
    }

    fun disconnect() {
        val n = node ?: return
        node = null
        deviceLabel = null
        deviceName = null
        Thread { try { n.stop() } catch (e: Exception) { } }.start()
        RadioService.stop(app)
        main.post { messages.clear(); listeners.forEach { it.onState() } }
    }

    /** USB module unplugged while running. */
    fun radioLost() {
        val n = node ?: return
        if (n.radio == null) return
        deviceName = null
        Thread { try { n.detachRadio() } catch (e: Exception) { } }.start()
        RadioService.stop(app)
        main.post { addSystem("radio module disconnected - log out and start again after plugging it back in") }
    }

    // -- messages -----------------------------------------------------------------------

    fun addMessage(m: Msg) {
        messages.add(m)
        if (messages.size > 2000) messages.subList(0, messages.size - 1500).clear()
    }

    fun addSystem(text: String) {
        val m = Msg(System.currentTimeMillis() / 1000.0, "sys", "sys", "all", null, text)
        addMessage(m)
    }

    private fun notifyIfHidden(m: Msg) {
        if (visible || m.direction != "rx" || m.kind in setOf("undecoded", "locked")) return
        unread++
        RadioService.update(app, unread, "${m.sender ?: "?"}: ${m.text}")
    }

    fun profile(): AirProfile = Protocol.profile

    fun openChat(ctx: Context) {
        val i = Intent(ctx, ChatActivity::class.java)
        if (ctx !is android.app.Activity) i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(i)
    }

    companion object {
        val SDK = Build.VERSION.SDK_INT
    }
}
