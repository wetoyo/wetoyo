/*
 * verify_shortcut.c - independent check of a signed .shortcut file.
 *
 * Uses libshortcutsign (https://github.com/0xilis/libshortcutsign) to:
 *   1. identify the container format (unsigned / contact-signed / iCloud-signed),
 *   2. verify the AEA signature and the signing certificate chain,
 *   3. extract the embedded Shortcut.wflow (binary plist) to <out.plist>.
 *
 * Usage: verify_shortcut <signed.shortcut> <out.plist>
 * Exit status 0 only if the signature verifies AND extraction succeeds.
 */
#include <stdio.h>
#include <stdlib.h>
#include <libshortcutsign.h>

static const char *fmt_name(SSFormat f) {
    switch (f) {
        case SHORTCUT_UNSIGNED: return "unsigned";
        case SHORTCUT_SIGNED_CONTACT: return "contact-signed";
        case SHORTCUT_SIGNED_ICLOUD: return "iCloud-signed (anyone)";
        default: return "unknown";
    }
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <signed.shortcut> <out.plist>\n", argv[0]);
        return 2;
    }
    FILE *fp = fopen(argv[1], "rb");
    if (!fp) { perror("open"); return 2; }
    fseek(fp, 0, SEEK_END);
    size_t size = (size_t)ftell(fp);
    fseek(fp, 0, SEEK_SET);
    uint8_t *buf = malloc(size);
    if (fread(buf, 1, size, fp) != size) { fprintf(stderr, "short read\n"); return 2; }
    fclose(fp);

    printf("file:      %s (%zu bytes)\n", argv[1], size);
    printf("format:    %s\n", fmt_name(get_shortcut_format(buf, size)));
    print_shortcut_cert_info(buf, size);

    int v = verify_signed_shortcut_buffer(buf, size);
    printf("signature: %s\n", v == 0 ? "VALID" : "INVALID");

    int e = extract_signed_shortcut(argv[1], argv[2]);
    printf("extract:   %s\n", e == 0 ? argv[2] : "FAILED");

    free(buf);
    return (v == 0 && e == 0) ? 0 : 1;
}
