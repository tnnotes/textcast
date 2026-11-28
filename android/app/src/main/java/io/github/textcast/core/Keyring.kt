package io.github.textcast.core

import java.io.File

/**
 * Identity keyring: own streams, followed streams, groups and contacts.
 * Port of lora_text/keyring.py; the file contents (JSON inside the vault) are
 * the same, so a .ltid backup made on the desktop opens here and back.
 *
 * Strings exchanged out of band:
 *   identity: lid1:<handle>:<base32 public key>
 *   stream:   lts3:<handle>:<pub>:<label>:<secret>:<signature>
 *   group:    ltg2:<label>:<secret>:<handle>=<pub>,...
 */
open class KeyException(msg: String) : IllegalArgumentException(msg)
class LockedException(msg: String) : KeyException(msg)

data class Opened(
    val kind: String,            // signed | sealed | join | direct
    val source: String,          // stream label or group label
    val sender: String,
    val text: String,
    val counter: Long,
    val pub: ByteArray? = null,
    val verified: Boolean = false,
    val group: Boolean = false,
    val inner: String? = null,   // direct: text | resend | receipt
    val acks: List<Long> = emptyList(),
)

/** Direct (one-to-one) messages: the plaintext inside the sealed frame starts with this byte. */
object Dm {
    const val TEXT = 1
    const val RESEND = 2
    const val RECEIPT = 3
    const val MAX_ACKS = 4
    val INNER = mapOf(TEXT to "text", RESEND to "resend", RECEIPT to "receipt")
    /** Id of a direct message, as receipts confirm it: the low 32 bits of its counter. */
    fun id(counter: Long): Long = counter and 0xFFFFFFFFL
}

object Ids {
    private val NICK = Regex("^[^\\s:#,=]{1,16}$")

    fun checkNick(nick: String?) {
        if (nick != null && !NICK.matches(nick)) throw KeyException("nick must be 1-16 characters, no spaces or : # , =")
    }

    /** 60-bit fingerprint for comparing keys in person, e.g. K7QX-M2PA-9TRE. */
    fun fingerprint(pub: ByteArray): String {
        val f = Bytes.b32(Bytes.sha256(pub)).uppercase().substring(0, 12)
        return "${f.substring(0, 4)}-${f.substring(4, 8)}-${f.substring(8)}"
    }

    fun tagOf(pub: ByteArray): String = fingerprint(pub).substring(0, 4)
    fun makeHandle(nick: String, pub: ByteArray): String = "$nick#${tagOf(pub)}"
    fun handleTag(handle: String): String = handle.substringAfterLast('#', handle).uppercase()

    fun parseLid(s: String): Pair<String, ByteArray> {
        val parts = s.trim().split(":")
        if (parts.size != 3 || parts[0] != "lid1") throw KeyException("not an identity string (lid1:handle:key)")
        val pub = try { Bytes.unb32(parts[2]) } catch (e: IllegalArgumentException) { throw KeyException("identity string is damaged") }
        if (pub.size != 32 || handleTag(parts[1]) != tagOf(pub)) throw KeyException("identity string is damaged (tag does not match key)")
        return parts[1] to pub
    }
}

class Keyring(val file: File, me: String? = null, passphrase: String? = null) {
    val encrypted = file.name.endsWith(".lkey")
    var me: String? = me; private set
    var locked = false; private set
    lateinit var data: MutableMap<String, Any?>; private set
    lateinit var pub: ByteArray; private set
    private var vault: Vault? = null
    private val cache = HashMap<String, Crypto.StreamKey>()
    private val seenCtrl = HashSet<String>()
    private val pairs = HashMap<String, Pair<ByteArray, ByteArray>?>()   // contact key -> (send, receive)
    private var dirty = false
    private var numberCached: String? = null
    private var findableCached = true
    private var lastSave = 0.0
    val floor = Crypto.startupFloor()

    init { load(passphrase) }

    private fun empty(me: String?): MutableMap<String, Any?> = linkedMapOf(
        "me" to me, "own" to LinkedHashMap<String, Any?>(), "follow" to LinkedHashMap<String, Any?>(),
        "groups" to LinkedHashMap<String, Any?>(), "contacts" to LinkedHashMap<String, Any?>())

    private fun load(passphrase: String?) {
        val d = empty(me)
        var dirtyNow = false
        if (file.exists()) {
            val raw = file.readBytes()
            val loaded: MutableMap<String, Any?>
            if (encrypted) {
                if (passphrase == null) throw LockedException("passphrase required")
                val (l, v) = Vault.open(raw, passphrase)
                loaded = l; vault = v
            } else {
                loaded = Json.parseObject(Bytes.utf8Lenient(raw))
            }
            for (k in listOf("own", "follow", "groups", "contacts")) d.obj(k).putAll(loaded[k].asMap())
            d["id_seed"] = loaded["id_seed"]
            val stored = loaded["me"] as? String
            val want = me
            if (stored != null && want != null && !stored.equals(want, ignoreCase = true))
                throw KeyException("${file.name} belongs to callsign '$stored', not '$want'")
            d["me"] = stored ?: want
            d["log_secret"] = loaded["log_secret"]
            // keep keys this version does not know about (written by newer apps)
            for ((k, v) in loaded) if (k !in d) d[k] = v
        } else {
            if (encrypted) {
                if (passphrase.isNullOrEmpty()) throw KeyException("a passphrase is needed to create a protected identity")
                vault = Vault.create(passphrase)
            }
            dirtyNow = true
        }
        if ((d["log_secret"] as? String).isNullOrEmpty()) { d["log_secret"] = Bytes.b32(Bytes.random(32)); dirtyNow = true }
        if ((d["id_seed"] as? String).isNullOrEmpty()) { d["id_seed"] = Bytes.b32(Bytes.random(32)); dirtyNow = true }
        for (g in d.obj("groups").values) {             // upgrade old member format
            val members = g.asMap().obj("members")
            for ((m, v) in members.entries.toList())
                if (v is Number) members[m] = linkedMapOf<String, Any?>("i" to v.toLong(), "pub" to null, "name" to m)
        }
        Ids.checkNick(d["me"] as? String)
        data = d
        me = d["me"] as? String
        locked = false
        pub = Ed25519.publicKey(Bytes.unb32(d["id_seed"] as String))
        findableCached = d["findable"] != false          // kept while locked
        if (dirtyNow) save()
    }

    // -- persistence ---------------------------------------------------------

    @Synchronized fun save() {
        if (locked) throw LockedException("identity is locked")
        val tmp = File(file.path + ".tmp")
        if (encrypted) tmp.writeBytes(vault!!.seal(data)) else tmp.writeText(Json.stringify(data, 2))
        if (!tmp.renameTo(file)) { file.delete(); if (!tmp.renameTo(file)) throw java.io.IOException("cannot write ${file.name}") }
        dirty = false
        lastSave = MONOTONIC.now()
    }

    /** Receive counters changed: save at most every 30 s. */
    fun touch() {
        dirty = true
        if (MONOTONIC.now() - lastSave >= 30.0) save()
    }

    fun close() { if (dirty && !locked) try { save() } catch (e: Exception) { } }

    fun wipe() {
        if (locked) return
        save()
        data = empty(me)
        vault = null
        cache.clear()
        pairs.clear()
        locked = true
    }

    fun unlock(passphrase: String) = load(passphrase)

    fun changePassphrase(new: String) {
        if (!encrypted) throw KeyException("this identity is not passphrase-protected")
        requireUnlocked()
        vault = Vault.create(new)
        save()
    }

    /** Encrypted copy of the identity for moving it to another device. */
    fun exportBlob(passphrase: String): ByteArray {
        requireUnlocked()
        return Vault.create(passphrase).seal(data)
    }

    fun logCipher(): LogCipher? = if (locked) null else LogCipher(Bytes.unb32(data["log_secret"] as String))

    private fun requireUnlocked() { if (locked) throw LockedException("identity is locked - unlock first") }

    private fun key(secret: ByteArray): Crypto.StreamKey =
        cache.getOrPut(Bytes.hex(secret)) { Crypto.StreamKey(secret) }

    private fun section(name: String) = data.obj(name)

    // -- identity ------------------------------------------------------------

    val handle: String? get() = me?.let { Ids.makeHandle(it, pub) }

    fun lid(): String {
        val h = handle ?: throw KeyException("this identity has no nick")
        return "lid1:$h:${Bytes.b32(pub)}"
    }

    fun fingerprint(): String = Ids.fingerprint(pub)

    /** Your 10-digit TextCast number (computed from your key, cached). */
    val number: String get() {
        numberCached?.let { return it }
        val stored = data["number"] as? String
        val n = if (stored != null && data["number_pub"] == Bytes.b32(pub)) stored else Numbers.numberOf(pub).also {
            if (!locked) { data["number"] = it; data["number_pub"] = Bytes.b32(pub); dirty = true }
        }
        numberCached = n
        return n
    }

    /** Answer "who has my number?" lookups (default on). */
    val findable: Boolean get() = findableCached

    @Synchronized fun setFindable(on: Boolean) {
        requireUnlocked()
        data["findable"] = on; findableCached = on
        save()
    }

    private fun myKey(): String = handle?.lowercase() ?: throw KeyException("set a nick to post in groups")

    // -- contacts ----------------------------------------------------------------

    /** Save a contact from an identity string (pasted or scanned: the full key, so verified). */
    fun addContact(lid: String, name: String? = null, verified: Boolean = true): String {
        requireUnlocked()
        val (h, p) = Ids.parseLid(lid)
        val n = (name?.ifBlank { null } ?: h.substringBefore('#')).trim()
        val k = Bytes.b32(p)
        val old = section("contacts")[k]?.asMap()
        section("contacts")[k] = linkedMapOf<String, Any?>("name" to n, "handle" to h,
            "dm_out" to (old?.long("dm_out") ?: 0L), "dm_in" to (old?.long("dm_in") ?: 0L),
            "verified" to (verified || old?.bool("verified") == true),
            "number" to (old?.str("number") ?: Numbers.numberOf(p)))
        pairs.remove(k)
        save()
        return n
    }

    /** Save a contact found by number. Throws if the key doesn't have that number, or
     *  another saved contact already has it (somebody may be faking it). */
    fun addFound(pubKey: ByteArray, nick: String, number: String, name: String? = null): String {
        Ids.checkNick(nick)
        if (Numbers.numberOf(pubKey) != number) throw KeyException("the answer does not match that number")
        for ((k, v) in section("contacts")) {
            val c = v.asMap()
            if (k != Bytes.b32(pubKey) && contactNumber(k, c) == number)
                throw KeyException("${c.str("name")} already has number ${Numbers.format(number)} with a different key - check in person")
        }
        return addContact("lid1:${Ids.makeHandle(nick, pubKey)}:${Bytes.b32(pubKey)}", name, verified = false)
    }

    private fun contactNumber(k: String, c: MutableMap<String, Any?>): String =
        c.str("number") ?: Numbers.numberOf(Bytes.unb32(k)).also { c["number"] = it; dirty = true }

    class ContactInfo(val name: String, val handle: String, val number: String, val verified: Boolean)

    fun contactByNumber(number: String): ContactInfo? = contactInfo().firstOrNull { it.number == number }

    fun contactInfo(): List<ContactInfo> {
        val me = Bytes.b32(pub)
        return section("contacts").filter { it.key != me }.map { (k, v) ->
            val c = v.asMap()
            ContactInfo(c.str("name") ?: "", c.str("handle") ?: "", contactNumber(k, c), c["verified"] != false)
        }.sortedBy { it.name.lowercase() }
    }

    fun renameContact(handle: String, name: String) {
        requireUnlocked()
        if (name.isBlank()) throw KeyException("a name is needed")
        contact(handle).second["name"] = name.trim()
        save()
    }

    fun removeContact(name: String): Boolean {
        requireUnlocked()
        val c = section("contacts")
        for ((k, v) in c.entries.toList()) {
            val m = v.asMap()
            if (m.str("name") == name || m.str("handle")?.equals(name, true) == true) { c.remove(k); pairs.remove(k); save(); return true }
        }
        return false
    }

    /** (name, handle, fingerprint) */
    fun contacts(): List<Triple<String, String, String>> = section("contacts").map { (k, v) ->
        val m = v.asMap()
        Triple(m.str("name") ?: "", m.str("handle") ?: "", Ids.fingerprint(Bytes.unb32(k)))
    }

    fun display(handle: String, pub: ByteArray?): String {
        if (pub != null) section("contacts")[Bytes.b32(pub)]?.asMap()?.str("name")?.let { return it }
        return handle
    }

    // -- direct (one-to-one) messages (see keyring.py) -------------------------------
    //   pair = HMAC(a_me * A_peer, "lora-text/pair/" + h1 + "|" + h2)[:16]   (handles lower, sorted)
    //   each direction: memberSecret(pair, sender handle); ordinary SEALED frames

    /** (handle, name) of the contacts you can message, sorted by name. */
    fun directContacts(): List<Pair<String, String>> {
        val me = Bytes.b32(pub)
        return section("contacts").filterKeys { it != me }.values.map { v ->
            val m = v.asMap(); (m.str("handle") ?: "") to (m.str("name") ?: "")
        }.sortedBy { it.second.lowercase() }
    }

    private fun contact(handle: String): Pair<String, MutableMap<String, Any?>> {
        for ((k, v) in section("contacts")) {
            val m = v.asMap()
            if (m.str("handle")?.equals(handle, ignoreCase = true) == true) return k to m
        }
        throw KeyException("'$handle' is not one of your contacts")
    }

    private fun dmKeys(key: String, c: Map<String, Any?>): Pair<ByteArray, ByteArray>? = pairs.getOrPut(key) {
        val h = handle
        if (h == null || key == Bytes.b32(pub)) return@getOrPut null
        val s = Ed25519.sharedPoint(Bytes.unb32(data["id_seed"] as String), Bytes.unb32(key)) ?: return@getOrPut null
        val me = h.lowercase(); val peer = (c.str("handle") ?: return@getOrPut null).lowercase()
        val (h1, h2) = listOf(me, peer).sorted()
        val pair = Crypto.derive(s, "pair/$h1|$h2").copyOf(16)
        Crypto.memberSecret(pair, me) to Crypto.memberSecret(pair, peer)
    }

    private fun sealDm(handle: String, plaintext: ByteArray): Pair<ByteArray, Long> {
        requireUnlocked()
        val (key, c) = contact(handle)
        val keys = dmKeys(key, c) ?: throw KeyException("cannot message ${c.str("name")}: their identity key is not usable")
        val i = Crypto.nextCounter(c.long("dm_out"), floor)
        val mode = Protocol.makeMode(MsgType.SEALED, version = Protocol.PRIVATE_VERSION)
        val frame = Crypto.seal(key(keys.first), mode, i, plaintext, encrypt = true)
        c["dm_out"] = i
        save()
        return frame to i
    }

    /** Frame for a direct message to a contact, and its counter (see Dm.id). */
    @Synchronized fun sealDirect(handle: String, text: String, resend: Boolean = false): Pair<ByteArray, Long> {
        val body = Bytes.utf8(Protocol.normalize(text.trim()))
        val limit = Protocol.profile.maxPayload - Crypto.FRAME_OVERHEAD - 1
        if (body.size > limit) throw PayloadTooLong("${body.size} B of text > $limit B limit for a direct message")
        return sealDm(handle, Bytes.concat(byteArrayOf((if (resend) Dm.RESEND else Dm.TEXT).toByte()), body))
    }

    /** Receipt confirming up to Dm.MAX_ACKS direct messages from this contact. */
    @Synchronized fun makeReceipt(handle: String, ids: List<Long>): ByteArray {
        val parts = mutableListOf(byteArrayOf(Dm.RECEIPT.toByte()))
        for (i in ids.take(Dm.MAX_ACKS)) parts.add(Bytes.intBE(Dm.id(i).toInt()))
        return sealDm(handle, Bytes.concat(*parts.toTypedArray())).first
    }

    private fun openDirect(frame: ByteArray, t: Double?): Opened? {
        for ((key, v) in section("contacts").entries.toList()) {
            val c = v.asMap()
            val keys = dmKeys(key, c) ?: continue
            val k = key(keys.second)
            for (i in Crypto.matchingCounters(k, frame, c.long("dm_in"), t)) {
                val pt = Crypto.unseal(k, frame, i, true)
                if (pt.isEmpty()) continue
                val type = pt[0].toInt() and 0xFF
                val inner = Dm.INNER[type] ?: continue
                var text = ""
                val acks = ArrayList<Long>()
                if (type == Dm.RECEIPT) {
                    val n = pt.size - 1
                    if (n == 0 || n % 4 != 0 || n > 4 * Dm.MAX_ACKS) continue
                    for (j in 0 until n / 4) acks.add(Bytes.readIntBE(pt, 1 + 4 * j))
                } else {
                    text = Bytes.utf8Strict(pt, 1) ?: continue
                }
                c["dm_in"] = i
                touch()
                return Opened("direct", "@" + c.str("handle"), c.str("name") ?: "", text, i, Bytes.unb32(key),
                    verified = true, group = false, inner = inner, acks = acks)
            }
        }
        return null
    }

    // -- management ----------------------------------------------------------------

    private fun checkFree(label: String) {
        requireUnlocked()
        if (listOf("own", "follow", "groups").any { label in section(it) }) throw KeyException("label '$label' already exists")
        if (label.isEmpty() || label.any { it in ":# ,=" } || label in setOf("public", "all", "other"))
            throw KeyException("label must be one word without : # , = and not public/all/other")
    }

    fun newOwn(name: String): String {
        checkFree(name)
        if (me == null) throw KeyException("set a nick before creating a stream")
        section("own")[name] = linkedMapOf<String, Any?>("secret" to Bytes.b32(Bytes.random(16)), "last_i" to 0L)
        save()
        return share(name)
    }

    private fun streamMsg(handle: String, pubB32: String, label: String, secret: String): ByteArray =
        Bytes.utf8("lts3:$handle:$pubB32:$label:$secret")

    fun follow(share: String, label: String? = null): String {
        val parts = share.trim().split(":")
        val entry: MutableMap<String, Any?>
        val slabel: String; val secret: String
        if ((parts[0] == "lts3" && parts.size == 6) || (parts[0] == "lts2" && parts.size == 5)) {
            val h = parts[1]; val pubB32 = parts[2]; slabel = parts[3]; secret = parts[4]
            val p = try { Bytes.unb32(pubB32) } catch (e: IllegalArgumentException) { throw KeyException("stream string is damaged") }
            if (p.size != 32 || Ids.handleTag(h) != Ids.tagOf(p)) throw KeyException("stream string is damaged (tag does not match key)")
            entry = linkedMapOf("owner" to h, "pub" to pubB32, "verified" to false)
            if (parts[0] == "lts3") {
                val sig = try { Bytes.unb32(parts[5]) } catch (e: IllegalArgumentException) { ByteArray(0) }
                if (!Ed25519.verify(p, streamMsg(h, pubB32, slabel, secret), sig))
                    throw KeyException("stream string signature is invalid - it was not made by $h's key (or was altered)")
                entry["verified"] = true; entry["sig"] = parts[5]
            }
        } else if (parts.size == 3 && parts[0] == "lts1") {
            slabel = parts[1]; secret = parts[2]
            entry = linkedMapOf("owner" to parts[1], "pub" to null, "verified" to false)
        } else throw KeyException("not a stream string (lts3:…)")
        val l = label?.ifBlank { null } ?: slabel
        checkFree(l)
        try { Bytes.unb32(secret) } catch (e: IllegalArgumentException) { throw KeyException("stream secret is damaged") }
        entry["secret"] = secret; entry["last_i"] = 0L; entry["label"] = slabel
        section("follow")[l] = entry
        save()
        return l
    }

    fun newGroup(label: String): String {
        checkFree(label)
        val mk = myKey()
        section("groups")[label] = linkedMapOf<String, Any?>(
            "secret" to Bytes.b32(Bytes.random(16)),
            "members" to linkedMapOf<String, Any?>(mk to member(0, Bytes.b32(pub), handle!!)))
        save()
        return share(label)
    }

    private fun member(i: Long, pub: String?, name: String): MutableMap<String, Any?> =
        linkedMapOf("i" to i, "pub" to pub, "name" to name)

    fun joinGroup(share: String, label: String? = null): String {
        val parts = share.trim().split(":")
        if (parts.size != 4 || (parts[0] != "ltg1" && parts[0] != "ltg2")) throw KeyException("not a group string (ltg2:… or ltg1:…)")
        val l = label?.ifBlank { null } ?: parts[1]
        checkFree(l)
        try { Bytes.unb32(parts[2]) } catch (e: IllegalArgumentException) { throw KeyException("group secret is damaged") }
        val members = LinkedHashMap<String, Any?>()
        for (item in parts[3].split(",").map { it.trim() }.filter { it.isNotEmpty() }) {
            val name = item.substringBefore('=')
            val p = if ('=' in item) item.substringAfter('=') else ""
            members[name.lowercase()] = member(0, p.ifEmpty { null }, name)
        }
        if (me != null) members.getOrPut(myKey()) { member(0, Bytes.b32(pub), handle!!) }
        section("groups")[l] = linkedMapOf<String, Any?>("secret" to parts[2], "members" to members)
        save()
        return l
    }

    fun addMember(group: String, name: String) {
        requireUnlocked()
        val g = section("groups")[group]?.asMap() ?: throw KeyException("no group '$group'")
        val n = name.trim()
        g.obj("members").getOrPut(n.lowercase()) { member(0, null, n) }
        save()
    }

    fun remove(label: String): Boolean {
        requireUnlocked()
        for (s in listOf("own", "follow", "groups")) if (section(s).remove(label) != null) { save(); return true }
        return false
    }

    fun share(label: String): String {
        requireUnlocked()
        section("own")[label]?.asMap()?.let { o ->
            val pubB32 = Bytes.b32(pub); val secret = o.str("secret")!!
            val sig = Ed25519.sign(Bytes.unb32(data["id_seed"] as String), streamMsg(handle!!, pubB32, label, secret))
            return "lts3:$handle:$pubB32:$label:$secret:${Bytes.b32(sig)}"
        }
        section("groups")[label]?.asMap()?.let { g ->
            val members = g.obj("members").entries.sortedBy { it.key }
                .joinToString(",") { val m = it.value.asMap(); "${m.str("name")}=${m.str("pub") ?: ""}" }
            return "ltg2:$label:${g.str("secret")}:$members"
        }
        section("follow")[label]?.asMap()?.let { f ->
            val slabel = f.str("label") ?: label
            if (f.str("sig") != null) return "lts3:${f.str("owner")}:${f.str("pub")}:$slabel:${f.str("secret")}:${f.str("sig")}"
            if (f.str("pub") != null) return "lts2:${f.str("owner")}:${f.str("pub")}:$slabel:${f.str("secret")}"
            return "lts1:$slabel:${f.str("secret")}"
        }
        throw KeyException("no key '$label'")
    }

    /** (label, type, description) for every stream and group. */
    fun listing(): List<Triple<String, String, String>> {
        val out = ArrayList<Triple<String, String, String>>()
        for (n in section("own").keys) out.add(Triple(n, "own", "your stream (you post; followers read)"))
        for ((n, v) in section("follow")) {
            val f = v.asMap()
            out.add(Triple(n, "follow", "by ${f.str("owner") ?: n}" + if (f.bool("verified")) " ✓" else " (owner not verified)"))
        }
        for ((n, v) in section("groups")) {
            val names = v.asMap().obj("members").values.joinToString(", ") { it.asMap().str("name") ?: "?" }
            out.add(Triple(n, "group", "members: $names"))
        }
        return out
    }

    fun groups(): List<String> = section("groups").keys.toList()
    fun own(): List<String> = section("own").keys.toList()
    fun followed(): List<String> = section("follow").keys.toList()

    // -- sending ----------------------------------------------------------------------

    private class SenderState(val secret: ByteArray, val holder: MutableMap<String, Any?>, val field: String)

    private fun senderState(target: String): SenderState {
        section("own")[target]?.asMap()?.let { return SenderState(Bytes.unb32(it.str("secret")!!), it, "last_i") }
        section("groups")[target]?.asMap()?.let { g ->
            val mk = myKey()
            val ent = g.obj("members").getOrPut(mk) { member(0, Bytes.b32(pub), handle!!) }.asMap()
            return SenderState(Crypto.memberSecret(Bytes.unb32(g.str("secret")!!), mk), ent, "i")
        }
        throw KeyException("'$target' is not one of your streams or groups")
    }

    @Synchronized fun sealFor(target: String, text: String, signed: Boolean = false): ByteArray {
        requireUnlocked()
        val body = Bytes.utf8(Protocol.normalize(text.trim()))
        val limit = Protocol.profile.maxPayload - Crypto.FRAME_OVERHEAD
        if (body.size > limit) throw PayloadTooLong("${body.size} B of text > $limit B limit for $target")
        val st = senderState(target)
        val i = Crypto.nextCounter(st.holder.long(st.field), floor)
        val mode = Protocol.makeMode(if (signed) MsgType.SIGNED else MsgType.SEALED, version = Protocol.PRIVATE_VERSION)
        val frame = Crypto.seal(key(st.secret), mode, i, body, encrypt = !signed)
        st.holder[st.field] = i
        save()
        return frame
    }

    // -- group join announcements ----------------------------------------------------------

    private fun ctrlKeys(group: String): Pair<ByteArray, ByteArray> {
        val gs = Bytes.unb32(section("groups")[group].asMap().str("secret")!!)
        return Crypto.derive(gs, "control/enc") to Crypto.derive(gs, "control/mac")
    }

    @Synchronized fun makeJoin(group: String, minute: Long = Crypto.minuteNow(), nonce: ByteArray = Bytes.random(8)): ByteArray {
        requireUnlocked()
        myKey()
        val (enc, mac) = ctrlKeys(group)
        val pt = Bytes.concat(Bytes.utf8("J"), Bytes.intBE(minute.toInt()), pub, Bytes.utf8(handle!!))
        val ct = Crypto.chacha20Xor(enc.copyOf(32), Bytes.concat(nonce, ByteArray(4)), pt)
        val mode = Protocol.makeMode(MsgType.SEALED, version = Protocol.PRIVATE_VERSION)
        val body = Bytes.concat(nonce, ct)
        val tag = Bytes.hmacSha256(mac, Bytes.concat(byteArrayOf(mode.toByte()), body)).copyOf(Crypto.TAG_LEN)
        val frame = Bytes.concat(byteArrayOf(mode.toByte()), body, tag)
        if (frame.size > Protocol.profile.maxPayload)
            throw KeyException("the join announcement (${frame.size} B) does not fit this radio profile " +
                "(${Protocol.profile.maxPayload} B max) - members must add you by hand: $handle")
        return frame
    }

    private fun openCtrl(frame: ByteArray, t: Double?): Opened? {
        if (frame.size < 1 + 8 + 5 + 32 + 3 + Crypto.TAG_LEN) return null
        val body = frame.copyOfRange(1, frame.size - Crypto.TAG_LEN)
        val tag = frame.copyOfRange(frame.size - Crypto.TAG_LEN, frame.size)
        for (label in section("groups").keys.toList()) {
            val (enc, mac) = ctrlKeys(label)
            val want = Bytes.hmacSha256(mac, Bytes.concat(frame.copyOfRange(0, 1), body)).copyOf(Crypto.TAG_LEN)
            if (!Bytes.equal(want, tag)) continue
            val nonce = body.copyOfRange(0, 8)
            val nk = Bytes.hex(nonce)
            if (nk in seenCtrl) return null
            val pt = Crypto.chacha20Xor(enc.copyOf(32), Bytes.concat(nonce, ByteArray(4)), body.copyOfRange(8, body.size))
            if (pt[0] != 'J'.code.toByte() || Math.abs(Bytes.readIntBE(pt, 1) - Crypto.minuteNow(t)) > 10) return null
            val p = pt.copyOfRange(5, 37)
            val h = Bytes.utf8Lenient(pt.copyOfRange(37, pt.size))
            if (Ids.handleTag(h) != Ids.tagOf(p)) return null
            seenCtrl.add(nk)
            val members = section("groups")[label].asMap().obj("members")
            val k = h.lowercase()
            val old = members[k]?.asMap()
            val pB32 = Bytes.b32(p)
            if (old != null && old.str("pub") != null && old.str("pub") != pB32)
                return Opened("join", label, h, "rejected: $h announced a different key", 0, null, false, true)
            val isNew = old == null || old.str("pub") == null
            members[k] = member(old?.long("i") ?: 0, pB32, h)
            save()
            return Opened("join", label, h, if (isNew) "$h joined" else "$h is here", 0, p, false, true)
        }
        return null
    }

    // -- receiving ------------------------------------------------------------------------

    private class Rx(val source: String, val sender: String, val pub: ByteArray?, val secret: ByteArray,
                     val holder: MutableMap<String, Any?>, val field: String, val group: Boolean, val verified: Boolean)

    private fun receiveStreams(): List<Rx> {
        val out = ArrayList<Rx>()
        for ((label, v) in section("follow")) {
            val e = v.asMap()
            out.add(Rx(label, e.str("owner") ?: label, e.str("pub")?.let { Bytes.unb32(it) }, Bytes.unb32(e.str("secret")!!),
                e, "last_i", false, e.bool("verified")))
        }
        val mine = handle?.lowercase()
        for ((label, v) in section("groups")) {
            val g = v.asMap()
            val gs = Bytes.unb32(g.str("secret")!!)
            for ((k, mv) in g.obj("members")) {
                if (k == mine) continue
                val m = mv.asMap()
                out.add(Rx(label, m.str("name") ?: k, m.str("pub")?.let { Bytes.unb32(it) }, Crypto.memberSecret(gs, k),
                    m, "i", true, false))
            }
        }
        return out
    }

    @Synchronized fun open(frame: ByteArray, t: Double? = null): Opened? {
        if (locked || frame.isEmpty()) return null
        val (version, type, _) = Protocol.parseMode(frame[0].toInt() and 0xFF)
        if (version != Protocol.PRIVATE_VERSION || (type != MsgType.SIGNED && type != MsgType.SEALED) ||
            frame.size < Crypto.FRAME_OVERHEAD) return null
        val encrypted = type == MsgType.SEALED
        if (encrypted) {
            openCtrl(frame, t)?.let { return it }
            openDirect(frame, t)?.let { return it }
        }
        for (rx in receiveStreams()) {
            val k = key(rx.secret)
            for (i in Crypto.matchingCounters(k, frame, rx.holder.long(rx.field), t)) {
                val text = Bytes.utf8Strict(Crypto.unseal(k, frame, i, encrypted)) ?: continue
                rx.holder[rx.field] = i
                touch()
                return Opened(if (encrypted) "sealed" else "signed", rx.source, rx.sender, text, i, rx.pub, rx.verified, rx.group)
            }
        }
        return null
    }

    companion object {
        fun maxText(private: Boolean): Int =
            Protocol.profile.maxPayload - if (private) Crypto.FRAME_OVERHEAD else 1
    }
}
