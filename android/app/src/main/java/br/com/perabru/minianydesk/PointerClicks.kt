package br.com.perabru.minianydesk

import org.json.JSONObject

/** One immutable capture/point snapshot; button strokes are always ordered down then up. */
internal object PointerClicks {
    fun sequence(button: String, count: Int, captureId: String, point: ScreenGeometry.Point): List<JSONObject> {
        require(button in setOf("left", "middle", "right") && count in 1..2)
        require(captureId.isNotBlank() && captureId.length <= 256 && captureId.none { it.code < 32 || it.code == 127 })
        require(point.x.isFinite() && point.y.isFinite() && point.x in 0.0..1.0 && point.y in 0.0..1.0)
        fun command(action: String) = JSONObject().put("type", "input").put("action", action)
            .put("captureId", captureId).put("x", point.x).put("y", point.y)
        return buildList {
            add(command("move"))
            repeat(count) {
                add(command("button").put("button", button).put("down", true))
                add(command("button").put("button", button).put("down", false))
            }
        }
    }
}
