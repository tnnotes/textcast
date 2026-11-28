package io.github.textcast.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import io.github.textcast.R

/**
 * Keeps the app (and the radio) running while the screen is off or another
 * app is in front: a foreground service with a notification, plus a partial
 * wake lock so the USB reader keeps receiving.
 */
class RadioService : Service() {
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val n = build(this, intent?.getIntExtra(EXTRA_UNREAD, 0) ?: 0, intent?.getStringExtra(EXTRA_LAST))
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            else startForeground(ID, n)
        } catch (e: Exception) {
            // Android refused the foreground service: keep going, the app works while visible
        }
        if (wake == null) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "TextCast:radio").apply {
                setReferenceCounted(false)
                acquire()
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        try { wake?.release() } catch (e: Exception) { }
        wake = null
        super.onDestroy()
    }

    companion object {
        private const val ID = 1
        private const val CHANNEL = "radio"
        private const val EXTRA_UNREAD = "unread"
        private const val EXTRA_LAST = "last"

        private fun channel(ctx: Context) {
            if (Build.VERSION.SDK_INT >= 26) {
                val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                if (nm.getNotificationChannel(CHANNEL) == null)
                    nm.createNotificationChannel(NotificationChannel(CHANNEL, ctx.getString(R.string.notif_channel),
                        NotificationManager.IMPORTANCE_LOW))
            }
        }

        private fun build(ctx: Context, unread: Int, last: String?): Notification {
            channel(ctx)
            val s = ctx.session
            val open = Intent(ctx, ChatsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
            val flags = if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0
            val pi = PendingIntent.getActivity(ctx, 0, open, flags or PendingIntent.FLAG_UPDATE_CURRENT)
            val p = s.profile()
            val title = "${s.node?.handle ?: "TextCast"} · ${p.name} ${String.format(java.util.Locale.ROOT, "%.3f", p.freqMhz)} MHz"
            val text = if (unread > 0) "$unread new · ${last ?: ""}" else "Listening"
            @Suppress("DEPRECATION")
            val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(ctx, CHANNEL) else Notification.Builder(ctx)
            return b.setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(title)
                .setContentText(text)
                .setContentIntent(pi)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build()
        }

        fun start(ctx: Context) {
            val i = Intent(ctx, RadioService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
            } catch (e: Exception) { }
        }

        fun update(ctx: Context, unread: Int, last: String?) {
            if (ctx.session.node?.radio == null) return
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            try { nm.notify(ID, build(ctx, unread, last)) } catch (e: Exception) { }
        }

        fun stop(ctx: Context) {
            try { ctx.stopService(Intent(ctx, RadioService::class.java)) } catch (e: Exception) { }
        }
    }
}
