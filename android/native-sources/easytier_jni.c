/*
 * MiniAnyDesk's deliberately small JNI adapter for EasyTier's stable C FFI.
 * Link against libeasytier_ffi.so. No Android logger or Rust JNI dependency.
 *
 * Keep this class name in R8/ProGuard:
 *     -keep class com.easytier.jni.EasyTierJNI { *; }
 *
 * All methods serialize access to upstream's process-global instance manager.
 * Error messages are intentionally generic: TOML parser errors can contain a
 * source excerpt, including the private pairing secret. Neither config nor
 * upstream errors are logged or returned through this adapter.
 */
#include <jni.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    const char *key;
    const char *value;
} EasyTierKeyValuePair;

extern int parse_config(const char *config);
extern int run_network_instance(const char *config);
extern int retain_network_instance(const char *const *instance_names, size_t length);
extern int collect_network_infos(EasyTierKeyValuePair *infos, size_t max_length);
extern void free_string(const char *string);

enum {
    MAX_CONFIG_UNITS = 128 * 1024,
    MAX_INFO_INSTANCES = 100,
    MAX_INFO_BYTES = 8 * 1024 * 1024,
    MAX_ERROR_BYTES = 192
};

static pthread_mutex_t manager_lock = PTHREAD_MUTEX_INITIALIZER;
static char last_error[MAX_ERROR_BYTES];

/* All accesses to last_error occur while manager_lock is held. */
static void set_error(const char *message) {
    size_t length = strlen(message);
    if (length >= sizeof last_error) length = sizeof last_error - 1;
    memcpy(last_error, message, length);
    last_error[length] = '\0';
}

static void clear_error(void) {
    memset(last_error, 0, sizeof last_error);
}

static void erase_and_free(char *bytes, size_t length) {
    if (bytes != NULL) {
        volatile unsigned char *p = (volatile unsigned char *)bytes;
        while (length-- != 0) *p++ = 0;
        free(bytes);
    }
}

/* JNI's GetStringUTFChars uses modified UTF-8, whereas Rust expects standard
 * UTF-8. Convert UTF-16 explicitly, accepting astral Unicode and rejecting NUL
 * and unpaired surrogates before crossing the NUL-terminated C boundary. */
static char *copy_config_utf8(JNIEnv *env, jstring source, size_t *byte_length) {
    if (source == NULL) {
        set_error("A network configuration is required.");
        return NULL;
    }
    jsize length = (*env)->GetStringLength(env, source);
    if (length < 1 || length > MAX_CONFIG_UNITS) {
        set_error("The network configuration is empty or too large.");
        return NULL;
    }
    const jchar *units = (*env)->GetStringChars(env, source, NULL);
    if (units == NULL) {
        set_error("Not enough memory to prepare the network.");
        return NULL; /* Preserve any OutOfMemoryError raised by the JVM. */
    }
    size_t capacity = (size_t)length * 3 + 1;
    char *result = (char *)malloc(capacity);
    if (result == NULL) {
        (*env)->ReleaseStringChars(env, source, units);
        set_error("Not enough memory to prepare the network.");
        return NULL;
    }
    size_t cursor = 0;
    int valid = 1;
    for (jsize i = 0; i < length; ++i) {
        uint32_t cp = units[i];
        if (cp == 0 || (cp >= 0xdc00 && cp <= 0xdfff)) {
            valid = 0;
            break;
        }
        if (cp >= 0xd800 && cp <= 0xdbff) {
            if (i + 1 >= length || units[i + 1] < 0xdc00 || units[i + 1] > 0xdfff) {
                valid = 0;
                break;
            }
            cp = 0x10000u + ((cp - 0xd800u) << 10) + (units[++i] - 0xdc00u);
        }
        if (cp < 0x80) {
            result[cursor++] = (char)cp;
        } else if (cp < 0x800) {
            result[cursor++] = (char)(0xc0u | (cp >> 6));
            result[cursor++] = (char)(0x80u | (cp & 0x3fu));
        } else if (cp < 0x10000) {
            result[cursor++] = (char)(0xe0u | (cp >> 12));
            result[cursor++] = (char)(0x80u | ((cp >> 6) & 0x3fu));
            result[cursor++] = (char)(0x80u | (cp & 0x3fu));
        } else {
            result[cursor++] = (char)(0xf0u | (cp >> 18));
            result[cursor++] = (char)(0x80u | ((cp >> 12) & 0x3fu));
            result[cursor++] = (char)(0x80u | ((cp >> 6) & 0x3fu));
            result[cursor++] = (char)(0x80u | (cp & 0x3fu));
        }
    }
    (*env)->ReleaseStringChars(env, source, units);
    if (!valid) {
        erase_and_free(result, capacity);
        set_error("The network configuration contains an invalid character.");
        return NULL;
    }
    result[cursor] = '\0';
    *byte_length = cursor;
    return result;
}

/* Standard UTF-8 -> Java UTF-16. In contrast, NewStringUTF is not suitable
 * for serde_json output containing a Unicode computer name such as an emoji. */
static jstring make_java_utf8(JNIEnv *env, const char *bytes, size_t length) {
    if (length > MAX_INFO_BYTES) {
        set_error("The network status is too large.");
        return NULL;
    }
    jchar *units = (jchar *)malloc((length + 1) * sizeof(jchar));
    if (units == NULL) {
        set_error("Not enough memory to read the network status.");
        return NULL;
    }
    size_t cursor = 0, count = 0;
    int valid = 1;
    while (cursor < length) {
        unsigned char lead = (unsigned char)bytes[cursor++];
        uint32_t cp;
        unsigned int extra;
        uint32_t minimum;
        if (lead < 0x80) { cp = lead; extra = 0; minimum = 0; }
        else if (lead >= 0xc2 && lead <= 0xdf) { cp = lead & 0x1fu; extra = 1; minimum = 0x80; }
        else if (lead >= 0xe0 && lead <= 0xef) { cp = lead & 0x0fu; extra = 2; minimum = 0x800; }
        else if (lead >= 0xf0 && lead <= 0xf4) { cp = lead & 7u; extra = 3; minimum = 0x10000; }
        else { valid = 0; break; }
        if (extra > length - cursor) { valid = 0; break; }
        for (unsigned int n = 0; n < extra; ++n) {
            unsigned char continuation = (unsigned char)bytes[cursor++];
            if ((continuation & 0xc0u) != 0x80u) { valid = 0; break; }
            cp = (cp << 6) | (continuation & 0x3fu);
        }
        if (!valid || cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
            valid = 0;
            break;
        }
        if (cp < 0x10000) units[count++] = (jchar)cp;
        else {
            cp -= 0x10000;
            units[count++] = (jchar)(0xd800u | (cp >> 10));
            units[count++] = (jchar)(0xdc00u | (cp & 0x3ffu));
        }
    }
    jstring result = NULL;
    if (valid) result = (*env)->NewString(env, units, (jsize)count);
    else set_error("The network status contains an invalid character.");
    free(units);
    return result;
}

typedef struct {
    char *bytes;
    size_t length;
    size_t capacity;
} JsonBuffer;

static int append_bytes(JsonBuffer *out, const char *bytes, size_t length) {
    if (length > MAX_INFO_BYTES || out->length > MAX_INFO_BYTES - length) return 0;
    size_t need = out->length + length + 1;
    if (need > out->capacity) {
        size_t capacity = out->capacity != 0 ? out->capacity : 256;
        while (capacity < need && capacity < MAX_INFO_BYTES + 1u) {
            capacity *= 2;
            if (capacity > MAX_INFO_BYTES + 1u) capacity = MAX_INFO_BYTES + 1u;
        }
        char *grown = (char *)realloc(out->bytes, capacity);
        if (grown == NULL) return 0;
        out->bytes = grown;
        out->capacity = capacity;
    }
    memcpy(out->bytes + out->length, bytes, length);
    out->length += length;
    out->bytes[out->length] = '\0';
    return 1;
}

static int append_json_key(JsonBuffer *out, const char *key, size_t length) {
    static const char hex[] = "0123456789abcdef";
    if (!append_bytes(out, "\"", 1)) return 0;
    for (size_t i = 0; i < length; ++i) {
        unsigned char byte = (unsigned char)key[i];
        if (byte == '"' || byte == '\\') {
            char escaped[2] = {'\\', (char)byte};
            if (!append_bytes(out, escaped, sizeof escaped)) return 0;
        } else if (byte < 0x20) {
            char escaped[6] = {'\\', 'u', '0', '0', hex[byte >> 4], hex[byte & 15]};
            if (!append_bytes(out, escaped, sizeof escaped)) return 0;
        } else if (!append_bytes(out, key + i, 1)) return 0;
    }
    return append_bytes(out, "\":", 2);
}

static jint call_config(JNIEnv *env, jstring config, int start) {
    pthread_mutex_lock(&manager_lock);
    clear_error();
    size_t byte_length = 0;
    char *utf8 = copy_config_utf8(env, config, &byte_length);
    if (utf8 == NULL) {
        pthread_mutex_unlock(&manager_lock);
        return -1;
    }
    int result = start ? run_network_instance(utf8) : parse_config(utf8);
    erase_and_free(utf8, byte_length + 1);
    if (result != 0) set_error(start
        ? "Unable to start the private network. Stop the previous connection and try again."
        : "The private network configuration is invalid.");
    pthread_mutex_unlock(&manager_lock);
    return (jint)result;
}

JNIEXPORT jint JNICALL Java_com_easytier_jni_EasyTierJNI_parseConfig(
        JNIEnv *env, jobject receiver, jstring config) {
    (void)receiver;
    return call_config(env, config, 0);
}

JNIEXPORT jint JNICALL Java_com_easytier_jni_EasyTierJNI_runNetworkInstance(
        JNIEnv *env, jobject receiver, jstring config) {
    (void)receiver;
    return call_config(env, config, 1);
}

JNIEXPORT jint JNICALL Java_com_easytier_jni_EasyTierJNI_stopAllInstances(
        JNIEnv *env, jobject receiver) {
    (void)env;
    (void)receiver;
    pthread_mutex_lock(&manager_lock);
    clear_error();
    int result = retain_network_instance(NULL, 0);
    if (result != 0) set_error("Unable to stop the private network.");
    pthread_mutex_unlock(&manager_lock);
    return (jint)result;
}

JNIEXPORT jint JNICALL Java_com_easytier_jni_EasyTierJNI_retainNetworkInstance(
        JNIEnv *env, jobject receiver, jobjectArray names) {
    (void)receiver;
    pthread_mutex_lock(&manager_lock);
    clear_error();
    char *buffers[MAX_INFO_INSTANCES] = {0};
    size_t lengths[MAX_INFO_INSTANCES] = {0};
    jsize count = names != NULL ? (*env)->GetArrayLength(env, names) : 0;
    jint result = -1;
    if (count < 0 || count > MAX_INFO_INSTANCES) {
        set_error("Too many private network instances.");
        goto cleanup;
    }
    for (jsize i = 0; i < count; ++i) {
        jstring name = (jstring)(*env)->GetObjectArrayElement(env, names, i);
        if ((*env)->ExceptionCheck(env)) goto cleanup;
        if (name == NULL || (*env)->GetStringLength(env, name) > 1024) {
            if (name != NULL) (*env)->DeleteLocalRef(env, name);
            set_error("A private network instance name is invalid.");
            goto cleanup;
        }
        buffers[i] = copy_config_utf8(env, name, lengths + i);
        (*env)->DeleteLocalRef(env, name);
        if (buffers[i] == NULL) goto cleanup;
    }
    result = (jint)retain_network_instance((const char *const *)buffers, (size_t)count);
    if (result != 0) set_error("Unable to update the private network instances.");
cleanup:
    for (int i = 0; i < MAX_INFO_INSTANCES; ++i) {
        erase_and_free(buffers[i], lengths[i] + 1);
    }
    pthread_mutex_unlock(&manager_lock);
    return result;
}

JNIEXPORT jstring JNICALL Java_com_easytier_jni_EasyTierJNI_collectNetworkInfos(
        JNIEnv *env, jobject receiver, jint requested_max) {
    (void)receiver;
    pthread_mutex_lock(&manager_lock);
    clear_error();
    EasyTierKeyValuePair infos[MAX_INFO_INSTANCES] = {{0}};
    JsonBuffer out = {0};
    jstring result = NULL;
    if (requested_max < 1 || requested_max > MAX_INFO_INSTANCES) {
        set_error("The private network status limit must be between 1 and 100.");
        goto cleanup;
    }
    int count = collect_network_infos(infos, (size_t)requested_max);
    if (count < 0 || count > requested_max) {
        set_error("Unable to read the private network status.");
        goto cleanup;
    }
    if (!append_bytes(&out, "{\"map\":{", 8)) goto allocation_error;
    for (int i = 0; i < count; ++i) {
        if (infos[i].key == NULL || infos[i].value == NULL) {
            set_error("The private network returned an incomplete status.");
            goto cleanup;
        }
        size_t key_length = strnlen(infos[i].key, MAX_INFO_BYTES + 1u);
        size_t value_length = strnlen(infos[i].value, MAX_INFO_BYTES + 1u);
        if (i != 0 && !append_bytes(&out, ",", 1)) goto allocation_error;
        if (!append_json_key(&out, infos[i].key, key_length)
                || !append_bytes(&out, infos[i].value, value_length)) goto allocation_error;
    }
    if (!append_bytes(&out, "}}", 2)) goto allocation_error;
    result = make_java_utf8(env, out.bytes, out.length);
    goto cleanup;
allocation_error:
    set_error("The private network status is too large or memory is unavailable.");
cleanup:
    /* Upstream may allocate early pairs and then return -1. Free all slots,
     * including that failure case; zero-initialization makes this safe. */
    for (int i = 0; i < MAX_INFO_INSTANCES; ++i) {
        if (infos[i].key != NULL) free_string(infos[i].key);
        if (infos[i].value != NULL) free_string(infos[i].value);
    }
    free(out.bytes);
    pthread_mutex_unlock(&manager_lock);
    return result;
}

JNIEXPORT jstring JNICALL Java_com_easytier_jni_EasyTierJNI_getLastError(
        JNIEnv *env, jobject receiver) {
    (void)receiver;
    pthread_mutex_lock(&manager_lock);
    jstring result = last_error[0] != '\0'
        ? (*env)->NewStringUTF(env, last_error) : NULL;
    pthread_mutex_unlock(&manager_lock);
    return result;
}
