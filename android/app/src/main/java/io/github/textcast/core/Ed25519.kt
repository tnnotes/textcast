package io.github.textcast.core

import java.math.BigInteger

/**
 * Minimal Ed25519 (RFC 8032) for identity keys: port of lora_text/ed25519.py.
 * Built-in Ed25519 needs Android 13, so this keeps the app on old phones.
 * Not constant-time (same caveat as the Python version).
 */
object Ed25519 {
    private val P: BigInteger = BigInteger.ONE.shiftLeft(255).subtract(BigInteger.valueOf(19))
    private val Q: BigInteger = BigInteger.ONE.shiftLeft(252).add(BigInteger("27742317777372353535851937790883648493"))
    private val TWO = BigInteger.valueOf(2)
    private fun inv(x: BigInteger) = x.modPow(P.subtract(TWO), P)
    private val D: BigInteger = BigInteger.valueOf(-121665).multiply(inv(BigInteger.valueOf(121666))).mod(P)
    private val SQRT_M1: BigInteger = TWO.modPow(P.subtract(BigInteger.ONE).divide(BigInteger.valueOf(4)), P)

    private class Pt(val x: BigInteger, val y: BigInteger, val z: BigInteger, val t: BigInteger)

    private fun recoverX(y: BigInteger, sign: Int): BigInteger? {
        if (y >= P) return null
        val y2 = y.multiply(y)
        val x2 = y2.subtract(BigInteger.ONE).multiply(inv(D.multiply(y2).add(BigInteger.ONE))).mod(P)
        if (x2.signum() == 0) return if (sign != 0) null else BigInteger.ZERO
        var x = x2.modPow(P.add(BigInteger.valueOf(3)).divide(BigInteger.valueOf(8)), P)
        if (x.multiply(x).subtract(x2).mod(P).signum() != 0) x = x.multiply(SQRT_M1).mod(P)
        if (x.multiply(x).subtract(x2).mod(P).signum() != 0) return null
        if (x.testBit(0) != (sign == 1)) x = P.subtract(x)
        return x
    }

    private val G: Pt = run {
        val gy = BigInteger.valueOf(4).multiply(inv(BigInteger.valueOf(5))).mod(P)
        val gx = recoverX(gy, 0)!!
        Pt(gx, gy, BigInteger.ONE, gx.multiply(gy).mod(P))
    }

    private fun add(p: Pt, q: Pt): Pt {
        val a = p.y.subtract(p.x).multiply(q.y.subtract(q.x)).mod(P)
        val b = p.y.add(p.x).multiply(q.y.add(q.x)).mod(P)
        val c = TWO.multiply(p.t).multiply(q.t).multiply(D).mod(P)
        val d = TWO.multiply(p.z).multiply(q.z).mod(P)
        val e = b.subtract(a); val f = d.subtract(c); val g = d.add(c); val h = b.add(a)
        return Pt(e.multiply(f).mod(P), g.multiply(h).mod(P), f.multiply(g).mod(P), e.multiply(h).mod(P))
    }

    private fun mul(s0: BigInteger, p0: Pt): Pt {
        var q = Pt(BigInteger.ZERO, BigInteger.ONE, BigInteger.ONE, BigInteger.ZERO)
        var p = p0
        var s = s0
        while (s.signum() > 0) {
            if (s.testBit(0)) q = add(q, p)
            p = add(p, p)
            s = s.shiftRight(1)
        }
        return q
    }

    private fun equal(p: Pt, q: Pt): Boolean =
        p.x.multiply(q.z).subtract(q.x.multiply(p.z)).mod(P).signum() == 0 &&
            p.y.multiply(q.z).subtract(q.y.multiply(p.z)).mod(P).signum() == 0

    private fun leToInt(b: ByteArray): BigInteger = BigInteger(1, b.reversedArray())

    private fun intToLe(v: BigInteger, n: Int): ByteArray {
        val be = v.toByteArray()
        val out = ByteArray(n)
        for (k in 0 until minOf(n, be.size)) out[k] = be[be.size - 1 - k]
        return out
    }

    private fun compress(p: Pt): ByteArray {
        val zi = inv(p.z)
        val x = p.x.multiply(zi).mod(P); val y = p.y.multiply(zi).mod(P)
        return intToLe(if (x.testBit(0)) y.setBit(255) else y, 32)
    }

    private fun decompress(s: ByteArray): Pt? {
        if (s.size != 32) return null
        var y = leToInt(s)
        val sign = if (y.testBit(255)) 1 else 0
        y = y.clearBit(255)
        val x = recoverX(y, sign) ?: return null
        return Pt(x, y, BigInteger.ONE, x.multiply(y).mod(P))
    }

    private fun expand(seed: ByteArray): Pair<BigInteger, ByteArray> {
        val h = Bytes.sha512(seed)
        var a = leToInt(h.copyOfRange(0, 32))
        a = a.and(BigInteger.ONE.shiftLeft(254).subtract(BigInteger.valueOf(8))).setBit(254)
        return a to h.copyOfRange(32, 64)
    }

    private fun hq(vararg parts: ByteArray): BigInteger = leToInt(Bytes.sha512(Bytes.concat(*parts))).mod(Q)

    fun publicKey(seed: ByteArray): ByteArray = compress(mul(expand(seed).first, G))

    fun sign(seed: ByteArray, msg: ByteArray): ByteArray {
        val (a, prefix) = expand(seed)
        val pub = compress(mul(a, G))
        val r = hq(prefix, msg)
        val rr = compress(mul(r, G))
        val s = r.add(hq(rr, pub, msg).multiply(a)).mod(Q)
        return Bytes.concat(rr, intToLe(s, 32))
    }

    fun verify(public: ByteArray, msg: ByteArray, signature: ByteArray): Boolean {
        if (public.size != 32 || signature.size != 64) return false
        val a = decompress(public) ?: return false
        val rBytes = signature.copyOfRange(0, 32)
        val r = decompress(rBytes) ?: return false
        val s = leToInt(signature.copyOfRange(32, 64))
        if (s >= Q) return false
        val h = hq(rBytes, public, msg)
        return equal(mul(s, G), add(r, mul(h, a)))
    }
}
