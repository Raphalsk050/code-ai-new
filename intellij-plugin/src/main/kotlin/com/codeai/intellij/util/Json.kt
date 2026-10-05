package com.codeai.intellij.util

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonNull
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.google.gson.JsonPrimitive

// The bridge and the panel both speak JSON whose shape the backend owns. It is
// kept as a tree and handed on untouched, so a field this plugin has never
// heard of still reaches the other side.

/** A JSON object from Kotlin values: strings, numbers, booleans, lists, maps, JSON, null. */
internal fun json(vararg fields: Pair<String, Any?>): JsonObject =
    JsonObject().also { target -> for ((key, value) in fields) target.add(key, toJson(value)) }

internal fun toJson(value: Any?): JsonElement = when (value) {
    null -> JsonNull.INSTANCE
    is JsonElement -> value
    is String -> JsonPrimitive(value)
    is Boolean -> JsonPrimitive(value)
    is Number -> JsonPrimitive(value)
    is Map<*, *> -> JsonObject().also { target ->
        for ((key, item) in value) target.add(key.toString(), toJson(item))
    }
    is Iterable<*> -> JsonArray().also { target -> for (item in value) target.add(toJson(item)) }
    is Array<*> -> JsonArray().also { target -> for (item in value) target.add(toJson(item)) }
    else -> JsonPrimitive(value.toString())
}

/** The object a line of text holds, or null when it is anything else. */
internal fun parseJsonObject(text: String): JsonObject? =
    try {
        JsonParser.parseString(text).takeIf { it.isJsonObject }?.asJsonObject
    } catch (_: RuntimeException) {
        null
    }

/** The value under `key`, unless it is missing or null. */
internal fun JsonObject.present(key: String): JsonElement? = get(key)?.takeUnless { it.isJsonNull }

/** The value under `key` when it is a string, as `typeof x === "string"` asks. */
internal fun JsonObject.string(key: String): String? =
    present(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString

/** The value under `key` as text, as `String(x ?? fallback)` gives it. */
internal fun JsonObject.text(key: String, fallback: String = ""): String {
    val value = present(key) ?: return fallback
    return if (value.isJsonPrimitive) value.asString else value.toString()
}

/** The value under `key` as a number, as `Number(x)` reads one; null when it is not one. */
internal fun JsonObject.number(key: String): Double? {
    val value = present(key)?.takeIf { it.isJsonPrimitive }?.asJsonPrimitive ?: return null
    return when {
        value.isNumber -> value.asDouble
        value.isBoolean -> if (value.asBoolean) 1.0 else 0.0
        else -> value.asString.trim().toDoubleOrNull()
    }
}

/** Whether the value under `key` is one JavaScript treats as true. */
internal fun JsonObject.truthy(key: String): Boolean {
    val value = present(key) ?: return false
    if (!value.isJsonPrimitive) return true
    val primitive = value.asJsonPrimitive
    return when {
        primitive.isBoolean -> primitive.asBoolean
        primitive.isNumber -> primitive.asDouble.let { it != 0.0 && !it.isNaN() }
        else -> primitive.asString.isNotEmpty()
    }
}

internal fun JsonObject.obj(key: String): JsonObject? = present(key)?.takeIf { it.isJsonObject }?.asJsonObject

internal fun JsonObject.array(key: String): JsonArray? = present(key)?.takeIf { it.isJsonArray }?.asJsonArray

/** The strings of the array under `key`; anything else in it is left out. */
internal fun JsonObject.strings(key: String): List<String> =
    array(key)?.mapNotNull { item ->
        item.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString
    } ?: emptyList()
