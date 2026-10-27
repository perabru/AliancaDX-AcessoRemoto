package br.com.perabru.minianydesk

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.AttributeSet
import android.util.Base64
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.hypot
import kotlin.math.roundToInt

/** A whole-desktop viewer. Incoming JPEGs are decoded off the UI thread, newest first. */
class RemoteScreenView @JvmOverloads constructor(
    context: Context,
    attributes: AttributeSet? = null,
    style: Int = 0
) : View(context, attributes, style) {
    var send: ((JSONObject) -> Unit)? = null
    var onFrameInfo: ((JSONObject) -> Unit)? = null
    var onFramePresented: ((JSONObject, Long, Long) -> Unit)? = null
    var onInputReleased: (() -> Unit)? = null
    var onFrameError: ((String) -> Unit)? = null
    var onZoomChanged: ((String) -> Unit)? = null
    var onDragChanged: ((Boolean) -> Unit)? = null
    val isDragActive: Boolean get() = dragHeld
    val zoomLabel: String get() = "${((displayedFit?.zoom ?: 1.0) * 100).roundToInt()}%"

    private data class Incoming(val generation: Long, val info: JSONObject, val encoded: String, val receivedAt: Long)
    private data class Decoded(val generation: Long, val info: JSONObject, val bitmap: Bitmap, val width: Int, val height: Int,
                               val receivedAt: Long, val decodeMs: Long)

    private val ui = Handler(Looper.getMainLooper())
    private val generation = AtomicLong(0)
    private val rawFrame = AtomicReference<Incoming?>(null)
    private val decodedFrame = AtomicReference<Decoded?>(null)
    private val publishPosted = AtomicBoolean(false)
    private val decoderLock = Any()
    private var executor: ExecutorService = newDecoder()
    private var decoding = false
    private val decodeErrorAt = AtomicLong(-ERROR_INTERVAL_MS)
    private var rendering: Decoded? = null
    private var displayed: Decoded? = null
    private var displayedFit: ScreenGeometry.Viewport? = null
    private var baseFit: ScreenGeometry.Fit? = null
    private var viewerBounds: ScreenGeometry.Fit? = null
    private var lastZoomLabel = "100%"
    private var notified: Decoded? = null
    private var inputAllowed = false
    private var activePointer = INVALID_POINTER
    private var dragHeld = false
    private var suppressGesture = false
    private var touchStart: ScreenGeometry.Point? = null
    private var touchMoved = false
    private var touchLastX = 0f
    private var touchLastY = 0f
    private var pinchPointerA = INVALID_POINTER
    private var pinchPointerB = INVALID_POINTER
    private var pinchDistance = 0.0
    private var pinchCenterX = 0.0
    private var pinchCenterY = 0.0
    private var lastPoint = ScreenGeometry.Point(0.5, 0.5)
    private var lastMoveAt = 0L
    private val heldKeys = LinkedHashSet<String>()
    private var textGeneration = 0L
    private var sendingTextStroke = false
    private var initialized = false
    private val pointerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(0, 122, 80) }
    private val pointerCenterPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(255, 255, 255) }
    private val imagePaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(165, 178, 199)
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 15f, resources.displayMetrics)
        textAlign = Paint.Align.CENTER
    }

    init {
        setBackgroundColor(Color.rgb(9, 16, 28))
        isFocusable = true
        isFocusableInTouchMode = true
        isClickable = true
        contentDescription = "Mousepad de tela inteira. Deslize em qualquer área para mover o cursor. Levante e recoloque o dedo para continuar. Use os botões para clicar ou ativar Arrastar; toque novamente em Arrastar para soltar. Dois dedos ampliam e movem a visualização."
        initialized = true
    }

    private fun newDecoder(): ExecutorService = Executors.newSingleThreadExecutor { task ->
        Thread(task, "MiniAnyDesk-frame").apply { isDaemon = true }
    }

    fun showFrame(message: JSONObject) {
        val frameGeneration = generation.get()
        val receivedAt = SystemClock.uptimeMillis()
        try {
            val encoded = message.opt("image") as? String
            if (encoded.isNullOrEmpty() || encoded.length > MAX_ENCODED_LENGTH) {
                reportDecodeError(frameGeneration)
                return
            }
            val captureId = message.opt("captureId") as? String
            if (dimension(message, "width", MAX_PHYSICAL_DIMENSION) == null ||
                dimension(message, "height", MAX_PHYSICAL_DIMENSION) == null ||
                captureId.isNullOrEmpty() || captureId.length > MAX_CAPTURE_ID_LENGTH ||
                captureId.any { it.code < 32 || it.code == 127 } ||
                (message.has("frameWidth") && dimension(message, "frameWidth", MAX_DIMENSION) == null) ||
                (message.has("frameHeight") && dimension(message, "frameHeight", MAX_DIMENSION) == null)) {
                reportDecodeError(frameGeneration)
                return
            }
            // Copy metadata without serializing a second copy of the large JPEG string.
            val metadata = JSONObject()
            val keys = message.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                if (key != "image") metadata.put(key, message.opt(key))
            }
            val copy = JSONObject(metadata.toString())
            if (frameGeneration != generation.get()) return
            rawFrame.set(Incoming(frameGeneration, copy, encoded, receivedAt))
            scheduleDecode()
        } catch (_: Exception) {
            // A malformed frame cannot change the displayed capture or input target.
            reportDecodeError(frameGeneration)
        } catch (_: OutOfMemoryError) {
            reportDecodeError(frameGeneration)
        }
    }

    private fun dimension(info: JSONObject, key: String, maximum: Int): Int? {
        val value = (info.opt(key) as? Number)?.toDouble() ?: return null
        return if (value.isFinite() && value >= 1 && value <= maximum && value % 1.0 == 0.0)
            value.toInt() else null
    }

    private fun scheduleDecode() {
        synchronized(decoderLock) {
            if (executor.isShutdown || decoding) return
            decoding = true
            val runningExecutor = executor
            runningExecutor.execute {
                try {
                    while (!runningExecutor.isShutdown) {
                        val next = synchronized(decoderLock) {
                            if (executor !== runningExecutor) null else rawFrame.getAndSet(null)
                        } ?: break
                        if (next.generation != generation.get()) continue
                        var bitmap: Bitmap? = null
                        try {
                            val decodingAt = SystemClock.uptimeMillis()
                            val bytes = Base64.decode(next.encoded, Base64.DEFAULT)
                            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                            if (bounds.outWidth !in 1..MAX_DIMENSION || bounds.outHeight !in 1..MAX_DIMENSION ||
                                bounds.outMimeType != "image/jpeg" || bounds.outWidth.toLong() * bounds.outHeight > MAX_SOURCE_PIXELS) {
                                reportDecodeError(next.generation)
                                continue
                            }
                            val expectedWidth = next.info.optInt("frameWidth", bounds.outWidth)
                            val expectedHeight = next.info.optInt("frameHeight", bounds.outHeight)
                            if (expectedWidth != bounds.outWidth || expectedHeight != bounds.outHeight) {
                                reportDecodeError(next.generation)
                                continue
                            }
                            val options = BitmapFactory.Options().apply {
                                inPreferredConfig = Bitmap.Config.ARGB_8888
                                var sample = 1
                                while (bounds.outWidth.toLong() * bounds.outHeight / (sample.toLong() * sample) > MAX_DECODED_PIXELS) sample *= 2
                                inSampleSize = sample
                            }
                            bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
                            if (bitmap == null) {
                                reportDecodeError(next.generation)
                                continue
                            }
                            val result = Decoded(next.generation, next.info, bitmap, bounds.outWidth, bounds.outHeight,
                                next.receivedAt, (SystemClock.uptimeMillis() - decodingAt).coerceAtLeast(0))
                            var superseded: Decoded? = null
                            val accepted = synchronized(decoderLock) {
                                if (executor !== runningExecutor || next.generation != generation.get() || runningExecutor.isShutdown) false
                                else {
                                    // A replaced executor cannot overwrite a newer executor's slot.
                                    superseded = decodedFrame.getAndSet(result)
                                    true
                                }
                            }
                            if (!accepted) {
                                bitmap.recycle()
                                bitmap = null
                                continue
                            }
                            // The slot/UI now owns this bitmap, even if clearFrame runs here.
                            bitmap = null
                            superseded?.bitmap?.recycle()
                            postDecoded()
                        } catch (_: Exception) {
                            reportDecodeError(next.generation)
                        } catch (_: OutOfMemoryError) {
                            reportDecodeError(next.generation)
                        } finally {
                            bitmap?.recycle()
                        }
                    }
                } finally {
                    synchronized(decoderLock) {
                        if (executor === runningExecutor) {
                            decoding = false
                            if (rawFrame.get() != null && !executor.isShutdown) scheduleDecode()
                        }
                    }
                }
            }
        }
    }

    private fun postDecoded() {
        if (publishPosted.compareAndSet(false, true)) ui.post(publishDecoded)
    }

    private val publishDecoded = Runnable {
        val frame = decodedFrame.getAndSet(null)
        publishPosted.set(false)
        if (frame != null) {
            if (frame.generation == generation.get() && isAttachedToWindow) {
                // A candidate not yet drawn has never reached Android's render thread.
                if (rendering !== displayed) rendering?.bitmap?.recycle()
                rendering = frame
                invalidate()
            } else frame.bitmap.recycle()
        }
        // A decoder may have replaced the slot while this UI callback ran.
        if (decodedFrame.get() != null) postDecoded()
    }

    private fun reportDecodeError(frameGeneration: Long) {
        val now = SystemClock.uptimeMillis()
        while (true) {
            val previous = decodeErrorAt.get()
            if (now - previous < ERROR_INTERVAL_MS) return
            if (decodeErrorAt.compareAndSet(previous, now)) break
        }
        ui.post {
            if (frameGeneration == generation.get() && isAttachedToWindow)
                onFrameError?.invoke("Não foi possível exibir um quadro da tela remota.")
        }
    }

    fun clearFrame() {
        // Increment immediately so a reader/decoder already in flight cannot restore an old screen.
        generation.incrementAndGet()
        decodeErrorAt.set(-ERROR_INTERVAL_MS)
        rawFrame.set(null)
        decodedFrame.getAndSet(null)?.bitmap?.recycle()
        runOnUi {
            releaseInput()
            if (rendering !== displayed) rendering?.bitmap?.recycle()
            rendering = null
            displayed = null
            displayedFit = null
            baseFit = null
            viewerBounds = null
            notified = null
            lastPoint = ScreenGeometry.Point(0.5, 0.5)
            notifyZoomChanged()
            invalidate()
        }
    }

    fun setInputAllowed(allowed: Boolean) {
        runOnUi {
            if (!allowed) releaseInput()
            inputAllowed = allowed
            invalidate()
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val frame = rendering?.takeIf { it.generation == generation.get() }
        val fit = frame?.let {
            ScreenGeometry.fit(it.width, it.height, width - paddingLeft - paddingRight, height - paddingTop - paddingBottom, paddingLeft, paddingTop)
        }
        if (frame == null || fit == null) {
            displayedFit = null
            baseFit = null
            viewerBounds = null
            notifyZoomChanged()
            canvas.drawText("Aguardando a tela remota", width / 2f, height / 2f - (textPaint.ascent() + textPaint.descent()) / 2, textPaint)
            return
        }
        if (displayed?.info?.optString("captureId") != frame.info.optString("captureId") ||
            displayed?.info?.optInt("width") != frame.info.optInt("width") ||
            displayed?.info?.optInt("height") != frame.info.optInt("height")) {
            releaseInput()
            displayedFit = null
            lastPoint = ScreenGeometry.Point(0.5, 0.5)
        }
        val bounds = ScreenGeometry.Fit(paddingLeft, paddingTop, width - paddingLeft - paddingRight, height - paddingTop - paddingBottom)
        val previous = displayedFit
        val viewport = ScreenGeometry.viewport(fit, bounds, previous?.zoom ?: 1.0, previous?.left, previous?.top) ?: return
        val checkpoint = canvas.save()
        canvas.clipRect(bounds.left, bounds.top, bounds.left + bounds.width, bounds.top + bounds.height)
        canvas.drawBitmap(frame.bitmap, null, RectF(viewport.left.toFloat(), viewport.top.toFloat(), (viewport.left + viewport.width).toFloat(), (viewport.top + viewport.height).toFloat()), imagePaint)
        if (inputAllowed) {
            val cursorX = (viewport.left + lastPoint.x * (viewport.width - 1)).toFloat()
            val cursorY = (viewport.top + lastPoint.y * (viewport.height - 1)).toFloat()
            canvas.drawCircle(cursorX, cursorY, 7f * resources.displayMetrics.density, pointerPaint)
            canvas.drawCircle(cursorX, cursorY, 2f * resources.displayMetrics.density, pointerCenterPaint)
        }
        canvas.restoreToCount(checkpoint)
        displayed = frame
        displayedFit = viewport
        baseFit = fit
        viewerBounds = bounds
        notifyZoomChanged()
        if (notified !== frame) {
            notified = frame
            // Decoder CPU time is reported separately; do not count it twice at the Host.
            val renderMs = (SystemClock.uptimeMillis() - frame.receivedAt - frame.decodeMs).coerceAtLeast(0)
            // Metadata becomes authoritative only after this exact frame has been drawn.
            ui.post {
                if (displayed === frame && frame.generation == generation.get()) {
                    val metadata = JSONObject(frame.info.toString())
                    onFrameInfo?.invoke(metadata)
                    if (displayed === frame && frame.generation == generation.get())
                        onFramePresented?.invoke(metadata, frame.decodeMs, renderMs)
                }
            }
        }
    }

    override fun onSizeChanged(width: Int, height: Int, oldWidth: Int, oldHeight: Int) {
        if (width != oldWidth || height != oldHeight) {
            releaseInput()
            displayedFit = null
            baseFit = null
            viewerBounds = null
            notifyZoomChanged()
        }
        super.onSizeChanged(width, height, oldWidth, oldHeight)
    }

    private fun ready(): Boolean = inputAllowed && isShown && windowVisibility == VISIBLE && displayedFit != null &&
        displayed?.generation == generation.get() && send != null && hasWindowFocus()

    /** Local viewing remains available when remote input is paused. */
    private fun canView(): Boolean = isShown && windowVisibility == VISIBLE && displayedFit != null &&
        displayed?.generation == generation.get() && hasWindowFocus()

    fun resetZoom() = runOnUi {
        releaseInput()
        val base = baseFit ?: return@runOnUi
        val bounds = viewerBounds ?: return@runOnUi
        displayedFit = ScreenGeometry.viewport(base, bounds)
        notifyZoomChanged()
        invalidate()
    }

    fun zoomIn() = zoomBy(1.35)
    fun zoomOut() = zoomBy(1.0 / 1.35)

    private fun zoomBy(scale: Double) = runOnUi {
        releaseInput()
        val bounds = viewerBounds ?: return@runOnUi
        transformViewport(scale, bounds.left + bounds.width / 2.0, bounds.top + bounds.height / 2.0)
    }

    private fun transformViewport(scale: Double, fromX: Double, fromY: Double, toX: Double = fromX, toY: Double = fromY) {
        val base = baseFit ?: return
        val bounds = viewerBounds ?: return
        val current = displayedFit ?: return
        val transformed = ScreenGeometry.transformViewport(base, bounds, current, scale, fromX, fromY, toX, toY) ?: return
        displayedFit = transformed
        notifyZoomChanged()
        invalidate()
    }

    private fun notifyZoomChanged() {
        val label = zoomLabel
        if (label != lastZoomLabel) {
            lastZoomLabel = label
            onZoomChanged?.invoke(label)
        }
    }

    private fun message(action: String, point: ScreenGeometry.Point? = null): JSONObject {
        return JSONObject().put("type", "input").put("action", action).apply {
            displayed?.info?.optString("captureId")?.let { put("captureId", it) }
            if (point != null) put("x", point.x).put("y", point.y)
        }
    }

    private fun emit(message: JSONObject): Boolean {
        return try {
            val sender = send ?: return false
            sender(message)
            true
        } catch (_: Exception) { false }
    }

    private fun move(point: ScreenGeometry.Point, force: Boolean = false) {
        lastPoint = point
        invalidate()
        val now = SystemClock.uptimeMillis()
        if (ready() && (force || now - lastMoveAt >= MOVE_INTERVAL_MS)) {
            emit(message("move", point))
            lastMoveAt = now
        }
    }

    /** Explicit drag latch: lifting a finger repositions the hand without dropping the item. */
    fun toggleDrag(): Boolean {
        if (dragHeld) {
            val accepted = emit(message("button", lastPoint).put("button", "left").put("down", false))
            setDragHeld(false)
            return accepted
        }
        if (!ready()) return false
        val frameGeneration = generation.get()
        val capture = displayed?.info?.optString("captureId")
        move(lastPoint, true)
        if (!ready() || frameGeneration != generation.get() || capture != displayed?.info?.optString("captureId")) return false
        // Arm before sending so a synchronous pause/clear during the callback can release
        // the just-issued down. Never resurrect a drag after that callback returns.
        setDragHeld(true)
        if (!dragHeld || !ready()) return false
        val accepted = emit(message("button", lastPoint).put("button", "left").put("down", true))
        if (!accepted || !ready() || frameGeneration != generation.get() || capture != displayed?.info?.optString("captureId")) {
            releaseInput()
            return false
        }
        return dragHeld
    }

    private fun setDragHeld(held: Boolean) {
        if (dragHeld == held) return
        dragHeld = held
        onDragChanged?.invoke(held)
        invalidate()
    }

    /** Relative motion covers the full viewer, including the bars around the PC image. */
    private fun moveFromTouch(x: Float, y: Float, force: Boolean = false) {
        val bounds = viewerBounds ?: return
        if (!x.isFinite() || !y.isFinite()) return
        val dx = x.toDouble() - touchLastX.toDouble()
        val dy = y.toDouble() - touchLastY.toDouble()
        touchLastX = x
        touchLastY = y
        if (dx == 0.0 && dy == 0.0) {
            if (force && touchMoved) move(lastPoint, true)
            return
        }
        touchMoved = true
        val point = ScreenGeometry.Point(
            (lastPoint.x + dx / maxOf(1, bounds.width)).coerceIn(0.0, 1.0),
            (lastPoint.y + dy / maxOf(1, bounds.height)).coerceIn(0.0, 1.0))
        move(point, force)
    }

    private fun updatePinchReference(event: MotionEvent): Boolean {
        val first = event.findPointerIndex(pinchPointerA)
        val second = event.findPointerIndex(pinchPointerB)
        if (first < 0 || second < 0) return false
        pinchCenterX = (event.getX(first).toDouble() + event.getX(second)) / 2.0
        pinchCenterY = (event.getY(first).toDouble() + event.getY(second)) / 2.0
        pinchDistance = hypot((event.getX(first) - event.getX(second)).toDouble(),
            (event.getY(first) - event.getY(second)).toDouble())
        return true
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                if (!canView() || !event.x.isFinite() || !event.y.isFinite()) return false
                suppressGesture = false
                textGeneration++
                requestFocus()
                parent?.requestDisallowInterceptTouchEvent(true)
                activePointer = event.getPointerId(0)
                touchLastX = event.x
                touchLastY = event.y
                touchMoved = false
                // Touching the pad never teleports or presses the remote mouse.
                touchStart = if (ready()) lastPoint else null
                return true
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                if (!canView()) return false
                if (event.pointerCount == 2) {
                    releaseInput()
                    suppressGesture = true
                    pinchPointerA = event.getPointerId(0)
                    pinchPointerB = event.getPointerId(1)
                    updatePinchReference(event)
                    parent?.requestDisallowInterceptTouchEvent(true)
                }
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                if (suppressGesture) {
                    if (!canView()) { releaseInput(); return true }
                    val oldX = pinchCenterX
                    val oldY = pinchCenterY
                    val oldDistance = pinchDistance
                    if (updatePinchReference(event) && oldDistance >= MIN_PINCH_DISTANCE && pinchDistance >= MIN_PINCH_DISTANCE) {
                        transformViewport(pinchDistance / oldDistance, oldX, oldY, pinchCenterX, pinchCenterY)
                    }
                    return true
                }
                val index = event.findPointerIndex(activePointer)
                if (index < 0 || !ready()) {
                    val consumed = activePointer != INVALID_POINTER
                    if (touchStart != null || dragHeld) releaseInput()
                    return consumed
                }
                if (touchStart != null) moveFromTouch(event.getX(index), event.getY(index))
                return true
            }
            MotionEvent.ACTION_UP -> {
                if (!suppressGesture && activePointer != INVALID_POINTER && ready()) {
                    val index = event.findPointerIndex(activePointer)
                    if (index >= 0 && touchStart != null) moveFromTouch(event.getX(index), event.getY(index), true)
                } else if (dragHeld) releaseInput()
                // A deliberate drag remains held across clutch gestures until its button is
                // pressed again. Cancellation, focus loss and pause always release it.
                clearTouchState()
                parent?.requestDisallowInterceptTouchEvent(false)
                return true
            }
            MotionEvent.ACTION_POINTER_UP -> {
                if (suppressGesture) {
                    val lifted = event.getPointerId(event.actionIndex)
                    if (lifted == pinchPointerA || lifted == pinchPointerB) {
                        pinchPointerA = INVALID_POINTER
                        pinchPointerB = INVALID_POINTER
                    }
                } else if (event.getPointerId(event.actionIndex) == activePointer) releaseInput()
                return true
            }
            MotionEvent.ACTION_CANCEL -> { releaseInput(); return true }
        }
        return super.onTouchEvent(event)
    }

    private fun clearTouchState() {
        activePointer = INVALID_POINTER
        touchStart = null
        touchMoved = false
        suppressGesture = false
        pinchPointerA = INVALID_POINTER
        pinchPointerB = INVALID_POINTER
        pinchDistance = 0.0
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    fun sendButtonTap(button: String = "right"): Boolean = sendButtonTaps(button, 1)

    fun sendDoubleClick(): Boolean = sendButtonTaps("left", 2)

    private fun sendButtonTaps(button: String, count: Int): Boolean {
        if (!ready() || button !in setOf("left", "middle", "right")) return false
        textGeneration++
        if (dragHeld) {
            emit(message("button", lastPoint).put("button", "left").put("down", false))
            setDragHeld(false)
        }
        // Button overlays are independent targets: a second finger can click while the
        // first keeps sliding on the pad. Do not discard that finger's relative anchor.
        val capture = displayed?.info?.optString("captureId")?.takeIf { it.isNotBlank() } ?: return false
        val point = lastPoint
        for (command in PointerClicks.sequence(button, count, capture, point)) {
            if (!ready() || displayed?.info?.optString("captureId") != capture) {
                releaseInput(); return false
            }
            if (!emit(command)) { releaseInput(); return false }
        }
        invalidate()
        return true
    }

    fun sendWheel(delta: Int): Boolean {
        if (!ready() || delta == 0) return false
        textGeneration++
        return emit(message("wheel", lastPoint).put("delta", delta.coerceIn(-20, 20)))
    }

    fun sendKey(key: String, down: Boolean): Boolean {
        if (key.isEmpty() || key.length > 30) return false
        if (!sendingTextStroke) textGeneration++
        if (down) {
            if (!ready()) return false
            if (heldKeys.contains(key)) return true
            if (!emit(message("key").put("key", key).put("down", true))) return false
            heldKeys.add(key)
            return true
        }
        if (!heldKeys.remove(key)) return false
        return emit(message("key").put("key", key).put("down", false))
    }

    fun sendTextASCII(text: String): Boolean {
        val keys = ScreenGeometry.asciiKeys(text) ?: return false
        if (!ready()) return false
        if (keys.isEmpty()) return true
        releaseInput()
        val token = ++textGeneration
        val frameGeneration = generation.get()
        val capture = displayed?.info?.optString("captureId")
        var index = 0
        val sendNext = object : Runnable {
            override fun run() {
                if (token != textGeneration || frameGeneration != generation.get() || !ready() ||
                    capture != displayed?.info?.optString("captureId") || index >= keys.size) return
                val stroke = keys[index++]
                sendingTextStroke = true
                val accepted: Boolean
                try {
                    if (stroke.shift) sendKey("shift", true)
                    accepted = sendKey(stroke.key, true)
                    if (accepted) sendKey(stroke.key, false)
                    if (stroke.shift) sendKey("shift", false)
                } finally {
                    sendingTextStroke = false
                }
                if (!accepted) releaseInput()
                else if (index < keys.size) ui.postDelayed(this, 12)
            }
        }
        ui.post(sendNext)
        return true
    }

    fun releaseInput() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            ui.post { releaseInput() }
            return
        }
        textGeneration++
        if (dragHeld) emit(message("button", lastPoint).put("button", "left").put("down", false))
        setDragHeld(false)
        clearTouchState()
        heldKeys.clear()
        emit(JSONObject().put("type", "input").put("action", "release"))
        parent?.requestDisallowInterceptTouchEvent(false)
        onInputReleased?.invoke()
    }

    override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        if (!hasWindowFocus) releaseInput()
        super.onWindowFocusChanged(hasWindowFocus)
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        // View's constructor may invoke this before our fields have initialized.
        if (initialized && !isShown) releaseInput()
    }

    override fun onWindowVisibilityChanged(visibility: Int) {
        super.onWindowVisibilityChanged(visibility)
        if (initialized && visibility != VISIBLE) releaseInput()
    }

    override fun onFocusChanged(gainFocus: Boolean, direction: Int, previouslyFocusedRect: android.graphics.Rect?) {
        if (!gainFocus) releaseInput()
        super.onFocusChanged(gainFocus, direction, previouslyFocusedRect)
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        synchronized(decoderLock) {
            if (executor.isShutdown) {
                executor = newDecoder()
                decoding = false
            }
        }
        if (rawFrame.get() != null) scheduleDecode()
    }

    override fun onDetachedFromWindow() {
        clearFrame()
        synchronized(decoderLock) { executor.shutdownNow() }
        super.onDetachedFromWindow()
    }

    private fun runOnUi(action: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) action() else ui.post(action)
    }

    companion object {
        private const val INVALID_POINTER = -1
        private const val MIN_PINCH_DISTANCE = 8.0
        private const val MOVE_INTERVAL_MS = 16L
        private const val ERROR_INTERVAL_MS = 3000L
        private const val MAX_CAPTURE_ID_LENGTH = 256
        private const val MAX_DIMENSION = 32768
        private const val MAX_PHYSICAL_DIMENSION = 1_000_000
        private const val MAX_ENCODED_LENGTH = 32 * 1024 * 1024
        private const val MAX_SOURCE_PIXELS = 64_000_000L
        private const val MAX_DECODED_PIXELS = 8_000_000L
    }
}
