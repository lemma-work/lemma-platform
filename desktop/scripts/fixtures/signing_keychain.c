#include <Security/Security.h>
#include <stdio.h>
#include <string.h>

#ifndef REVISION
#define REVISION 1
#endif

// A private test keychain lets changed binaries exercise real macOS ACLs without
// touching the login keychain or letting a consent dialog turn a failure green.
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    SecKeychainSetUserInteractionAllowed(false);
    SecKeychainRef keychain = NULL;
    OSStatus status;
    const char *password = "disposable-test-keychain";
    if (!strcmp(argv[1], "create")) {
        status = SecKeychainCreate(argv[2], (UInt32)strlen(password), password,
                                   false, NULL, &keychain);
    } else {
        status = SecKeychainOpen(argv[2], &keychain);
    }
    if (status != errSecSuccess) return 3;
    if (!strcmp(argv[1], "create")) {
        status = SecKeychainAddGenericPassword(keychain, 21, "work.lemma.qa-signing",
                                              4, "test", 7, "fixture", NULL);
        if (status != errSecSuccess) SecKeychainDelete(keychain);
    } else if (!strcmp(argv[1], "read")) {
        UInt32 length = 0;
        void *data = NULL;
        status = SecKeychainFindGenericPassword(keychain, 21, "work.lemma.qa-signing",
                                               4, "test", &length, &data, NULL);
        if (status == errSecSuccess) {
            if (length != 7 || memcmp(data, "fixture", 7)) status = errSecDecode;
            SecKeychainItemFreeContent(NULL, data);
        }
    } else if (!strcmp(argv[1], "delete")) {
        status = SecKeychainDelete(keychain);
    } else {
        status = errSecParam;
    }
    CFRelease(keychain);
    fprintf(stderr, "revision=%d status=%d\n", REVISION, (int)status);
    return status == errSecSuccess ? 0 : 1;
}
