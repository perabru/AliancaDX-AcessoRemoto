package br.com.perabru.minianydesk

import android.app.Activity
import android.app.AlertDialog
import android.app.Instrumentation
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.InputDevice
import android.view.MotionEvent
import android.widget.Button
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Framework-only regression entrypoint for an isolated, fresh debug installation.
 * Exercises the actual MainActivity consent buttons without a network connection.
 * Compilation is not evidence of execution; invoke this runner explicitly to run it.
 */
class ConsentTouchInstrumentation : Instrumentation() {
    private var activity: MainActivity? = null
    private val passed = JSONArray()

    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        start()
    }

    override fun onStart() {
        var successful = false
        val report = JSONObject().put("scope", "Actual Android MainActivity consent touch dispatch; no network")
        try {
            check(targetContext.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
                "Use an isolated debug target, never a release installation"
            }
            check(ComputerBook(targetContext).entries().isEmpty()) { "Use a fresh debug target without saved PCs" }
            activity = startActivitySync(Intent().setClassName(targetContext.packageName, MainActivity::class.java.name)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
            waitForIdleSync()
            checkFileConsent()
            checkCertificateConsent()
            successful = true
        } catch (error: Throwable) {
            report.put("error", "${error.javaClass.simpleName}: ${error.message.orEmpty().take(400)}")
        } finally {
            runOnMainSync {
                visibleDialogs().forEach { it.dismiss() }
                activity?.finish()
            }
            waitForIdleSync()
            report.put("passed", successful).put("scenarios", passed)
            finish(if (successful) Activity.RESULT_OK else Activity.RESULT_CANCELED,
                Bundle().apply { putString("report", report.toString()) })
        }
    }

    private fun checkFileConsent() {
        stage("file-consent-touch-filter")
        val accepted = AtomicInteger()
        val cancelled = AtomicInteger()
        val screen = requireNotNull(activity)
        val callback: (Uri?) -> Unit = { uri -> check(uri == null); cancelled.incrementAndGet() }
        val choose: () -> Unit = { accepted.incrementAndGet() }
        val method = MainActivity::class.java.declaredMethods.single { it.name == "confirmFile" }.apply { isAccessible = true }
        runOnMainSync { method.invoke(screen, "Synthetic file approval", "Local test only", callback, choose) }
        val button = awaitPositiveButton()
        runOnMainSync { check(button.filterTouchesWhenObscured) }
        tap(button, MotionEvent.FLAG_WINDOW_IS_OBSCURED)
        check(accepted.get() == 0 && cancelled.get() == 0) { "Obscured file approval was accepted" }
        runOnMainSync { check(visibleDialogs().single().isShowing) }
        tap(button, 0)
        await("Clean file approval did not complete") { accepted.get() == 1 }
        check(cancelled.get() == 0) { "Clean file approval was cancelled" }
        passed.put("actual_file_dialog_rejects_obscured_touch_and_accepts_clear_touch")
    }

    private fun checkCertificateConsent() {
        stage("certificate-consent-touch-filter")
        val screen = requireNotNull(activity)
        val endpoint = "consent-${UUID.randomUUID()}.invalid"
        val port = 64321
        val syntheticPin = "12".repeat(32)
        val key = ConnectionPins.preferenceKey(endpoint, port)
        val preferences = targetContext.getSharedPreferences("native_connection", Activity.MODE_PRIVATE)
        val complete = CountDownLatch(1)
        val approved = AtomicReference<Boolean?>()
        val failure = AtomicReference<Throwable?>()
        val method = MainActivity::class.java.declaredMethods.single { it.name == "approveCertificate" }.apply { isAccessible = true }
        val generation = MainActivity::class.java.getDeclaredField("sessionGeneration").apply { isAccessible = true }.getInt(screen)
        val worker = Thread({
            try { approved.set(method.invoke(screen, endpoint, port, syntheticPin, generation) as Boolean) }
            catch (error: Throwable) { failure.set(error) }
            finally { complete.countDown() }
        }, "MiniAnyDesk-consent-regression").apply { isDaemon = true }
        try {
            check(!preferences.contains(key))
            worker.start()
            val button = awaitPositiveButton()
            runOnMainSync { check(button.filterTouchesWhenObscured) }
            tap(button, MotionEvent.FLAG_WINDOW_IS_OBSCURED)
            check(complete.count == 1L && !preferences.contains(key)) { "Obscured certificate approval was accepted" }
            tap(button, 0)
            check(complete.await(5, TimeUnit.SECONDS)) { "Clean certificate approval did not complete" }
            failure.get()?.let { throw it }
            check(approved.get() == true && preferences.getString(key, null) == syntheticPin)
            passed.put("actual_certificate_dialog_rejects_obscured_touch_and_accepts_clear_touch")
        } finally {
            runOnMainSync { visibleDialogs().forEach { it.dismiss() } }
            if (worker.isAlive) worker.join(3_000)
            preferences.edit().remove(key).commit()
        }
    }

    private fun awaitPositiveButton(): Button {
        var found: Button? = null
        await("Consent dialog was not shown or laid out") {
            runOnMainSync {
                found = visibleDialogs().singleOrNull()?.getButton(AlertDialog.BUTTON_POSITIVE)
                    ?.takeIf { it.width > 0 && it.height > 0 }
            }
            found != null
        }
        return requireNotNull(found)
    }

    private fun tap(button: Button, flags: Int) {
        runOnMainSync {
            val down = SystemClock.uptimeMillis()
            val properties = MotionEvent.PointerProperties().apply { id = 0; toolType = MotionEvent.TOOL_TYPE_FINGER }
            val coordinates = MotionEvent.PointerCoords().apply {
                x = button.width / 2f; y = button.height / 2f; pressure = 1f; size = 1f
            }
            for (action in intArrayOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
                val event = MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, 1,
                    arrayOf(properties), arrayOf(coordinates), 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, flags)
                try { button.dispatchTouchEvent(event) } finally { event.recycle() }
            }
        }
        waitForIdleSync()
    }

    @Suppress("UNCHECKED_CAST")
    private fun visibleDialogs(): List<AlertDialog> {
        val screen = activity ?: return emptyList()
        val field = MainActivity::class.java.getDeclaredField("consentDialogs").apply { isAccessible = true }
        return (field.get(screen) as Set<AlertDialog>).filter { it.isShowing }
    }

    private fun await(message: String, predicate: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + 5_000
        while (!predicate()) {
            check(SystemClock.uptimeMillis() < deadline) { message }
            SystemClock.sleep(25)
        }
    }

    private fun stage(name: String) = sendStatus(0, Bundle().apply { putString("stage", name) })
}
