// Small CoreGraphics-only window lister used when the ScreenCaptureKit Swift
// helper cannot be rebuilt after a macOS/Xcode update.  Unlike the old helper,
// it lists all windows so the EZVIZ player remains selectable while
// TouchDesigner is the front application.
#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <string.h>

static void copy_string(CFTypeRef value, char output[1024]) {
    output[0] = '\0';
    if (value && CFGetTypeID(value) == CFStringGetTypeID()) {
        CFStringGetCString((CFStringRef)value, output, 1024, kCFStringEncodingUTF8);
    }
}

static void json_string(const char *value) {
    putchar('"');
    for (const unsigned char *cursor = (const unsigned char *)value; *cursor; ++cursor) {
        if (*cursor == '"' || *cursor == '\\') putchar('\\');
        if (*cursor >= 0x20) putchar(*cursor);
    }
    putchar('"');
}

int main(void) {
    CFArrayRef windows = CGWindowListCopyWindowInfo(
        kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID
    );
    if (!windows) return 1;
    printf("[");
    int emitted = 0;
    for (CFIndex index = 0; index < CFArrayGetCount(windows); ++index) {
        CFDictionaryRef window = (CFDictionaryRef)CFArrayGetValueAtIndex(windows, index);
        char owner[1024], title[1024];
        copy_string(CFDictionaryGetValue(window, kCGWindowOwnerName), owner);
        copy_string(CFDictionaryGetValue(window, kCGWindowName), title);
        if (!strstr(owner, "萤石") && !strstr(owner, "EZVIZ") && !strstr(owner, "ezviz") && !strstr(owner, "videogo") && !strstr(title, "萤石云视频")) continue;
        CGRect bounds = CGRectZero;
        CFTypeRef boundsValue = CFDictionaryGetValue(window, kCGWindowBounds);
        if (!boundsValue || !CGRectMakeWithDictionaryRepresentation((CFDictionaryRef)boundsValue, &bounds)) continue;
        if (bounds.size.width < 160 || bounds.size.height < 120) continue;
        int windowID = 0;
        CFNumberRef number = (CFNumberRef)CFDictionaryGetValue(window, kCGWindowNumber);
        if (!number || !CFNumberGetValue(number, kCFNumberIntType, &windowID)) continue;
        if (emitted++) printf(",");
        printf("{\"window_id\":%d,\"owner\":", windowID); json_string(owner);
        printf(",\"title\":"); json_string(title);
        printf(",\"width\":%.0f,\"height\":%.0f}", bounds.size.width, bounds.size.height);
    }
    printf("]\n");
    CFRelease(windows);
    return 0;
}
