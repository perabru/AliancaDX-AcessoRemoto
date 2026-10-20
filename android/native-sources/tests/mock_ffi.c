#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct { const char *key; const char *value; } Pair;
static int outstanding;
static int mode;

static char *owned(const char *s) {
    char *copy = strdup(s);
    if (copy == NULL) abort();
    ++outstanding;
    return copy;
}

void free_string(const char *s) {
    if (s != NULL) { --outstanding; free((void *)s); }
}

int parse_config(const char *s) {
    if (strcmp(s, "invalid=secret-pairing-value") == 0) return -1;
    if (strcmp(s, "Computador Ação 🖥") == 0) return 0;
    if (strcmp(s, "mode=partial") == 0) { mode = 1; return 0; }
    if (strcmp(s, "mode=empty") == 0) { mode = 2; return 0; }
    if (strcmp(s, "mode=normal") == 0) { mode = 0; return 0; }
    return -9;
}

int run_network_instance(const char *s) {
    return strcmp(s, "run=valid") == 0 ? 0 : -1;
}

int retain_network_instance(const char *const *names, size_t length) {
    if (length == 0) return 0;
    if (length == 2 && strcmp(names[0], "α") == 0 && strcmp(names[1], "🖥") == 0) return 0;
    return -1;
}

int collect_network_infos(Pair *out, size_t max) {
    if (outstanding != 0) {
        fputs("FFI strings leaked across a JNI call\n", stderr);
        abort();
    }
    if (max == 0 || mode == 2) return 0;
    out[0].key = owned("PC \"α\"\n🖥");
    out[0].value = owned("{\"running\":true,\"hostname\":\"Ação 🖥\"}");
    return mode == 1 ? -1 : 1;
}
