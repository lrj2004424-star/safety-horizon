// Persistent CoreGraphics fallback for the Tencent Android-container EZVIZ
// window.  It emits the same EZV1 + BGRA stream consumed by the Python camera
// adapter, while selecting only the requested app/window title.
#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef CGImageRef (*CreateWindowImageFn)(
    CGRect,
    CGWindowListOption,
    CGWindowID,
    CGWindowImageOption
);

static void copy_string(CFTypeRef value, char output[1024]) {
    output[0] = '\0';
    if (value && CFGetTypeID(value) == CFStringGetTypeID()) {
        CFStringGetCString((CFStringRef)value, output, 1024, kCFStringEncodingUTF8);
    }
}

static CGWindowID find_window(const char *required) {
    CFArrayRef windows = CGWindowListCopyWindowInfo(
        kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID
    );
    if (!windows) return 0;
    CGWindowID best = 0;
    double bestArea = 0.0;
    int bestPriority = -1;
    for (CFIndex index = 0; index < CFArrayGetCount(windows); ++index) {
        CFDictionaryRef window = (CFDictionaryRef)CFArrayGetValueAtIndex(windows, index);
        char owner[1024], title[1024];
        copy_string(CFDictionaryGetValue(window, kCGWindowOwnerName), owner);
        copy_string(CFDictionaryGetValue(window, kCGWindowName), title);
        if (strstr(owner, "TouchDesigner")) continue;
        int priority = 0;
        if (required && *required) {
            if (strstr(title, required)) priority = 2;
            else if (strstr(owner, required)) priority = 1;
            else continue;
        }
        if ((!required || !*required) &&
            !strstr(owner, "萤石") && !strstr(owner, "EZVIZ") &&
            !strstr(owner, "videogo") && !strstr(title, "萤石云视频")) continue;
        CGRect bounds = CGRectZero;
        CFTypeRef boundsValue = CFDictionaryGetValue(window, kCGWindowBounds);
        if (!boundsValue || !CGRectMakeWithDictionaryRepresentation((CFDictionaryRef)boundsValue, &bounds)) continue;
        if (bounds.size.width < 160 || bounds.size.height < 120) continue;
        int windowID = 0;
        CFNumberRef number = (CFNumberRef)CFDictionaryGetValue(window, kCGWindowNumber);
        if (!number || !CFNumberGetValue(number, kCFNumberIntType, &windowID)) continue;
        double area = bounds.size.width * bounds.size.height;
        if (priority > bestPriority || (priority == bestPriority && area > bestArea)) {
            best = (CGWindowID)windowID;
            bestArea = area;
            bestPriority = priority;
        }
    }
    CFRelease(windows);
    return best;
}

static CGImageRef capture(CreateWindowImageFn createImage, CGWindowID windowID) {
    return createImage(
        CGRectNull,
        kCGWindowListOptionIncludingWindow,
        windowID,
        kCGWindowImageBoundsIgnoreFraming | kCGWindowImageNominalResolution
    );
}

int main(int argc, char **argv) {
    const char *required = "萤石云视频";
    int maxWidth = 1280;
    int maxHeight = 960;
    int fps = 12;
    for (int index = 1; index < argc; ++index) {
        if (!strcmp(argv[index], "--title-contains") && index + 1 < argc) required = argv[++index];
        else if (!strcmp(argv[index], "--max-width") && index + 1 < argc) maxWidth = atoi(argv[++index]);
        else if (!strcmp(argv[index], "--max-height") && index + 1 < argc) maxHeight = atoi(argv[++index]);
        else if (!strcmp(argv[index], "--fps") && index + 1 < argc) fps = atoi(argv[++index]);
        else if (!strcmp(argv[index], "--bundle-id") && index + 1 < argc) ++index;
    }
    if (maxWidth < 2 || maxHeight < 2 || fps < 1 || fps > 30) return 2;
    CreateWindowImageFn createImage = (CreateWindowImageFn)dlsym(RTLD_DEFAULT, "CGWindowListCreateImage");
    if (!createImage) return 3;
    CGWindowID windowID = 0;
    CGImageRef first = NULL;
    for (int attempt = 0; attempt < 40 && !first; ++attempt) {
        windowID = find_window(required);
        if (windowID) first = capture(createImage, windowID);
        if (!first) usleep(250000);
    }
    if (!first) {
        fprintf(stderr, "没有找到可读取的萤石云视频窗口\n");
        return 4;
    }
    size_t sourceWidth = CGImageGetWidth(first);
    size_t sourceHeight = CGImageGetHeight(first);
    double scale = 1.0;
    if ((double)sourceWidth > maxWidth) scale = (double)maxWidth / sourceWidth;
    if ((double)sourceHeight * scale > maxHeight) scale = (double)maxHeight / sourceHeight;
    uint32_t width = (uint32_t)((size_t)(sourceWidth * scale) / 2 * 2);
    uint32_t height = (uint32_t)((size_t)(sourceHeight * scale) / 2 * 2);
    if (width < 2) width = 2;
    if (height < 2) height = 2;
    size_t bytesPerRow = (size_t)width * 4;
    uint8_t *buffer = calloc((size_t)height, bytesPerRow);
    CGColorSpaceRef colorSpace = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(
        buffer,
        width,
        height,
        8,
        bytesPerRow,
        colorSpace,
        kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little
    );
    CGColorSpaceRelease(colorSpace);
    if (!buffer || !context) return 5;
    setvbuf(stdout, NULL, _IONBF, 0);
    fwrite("EZV1", 1, 4, stdout);
    fwrite(&width, sizeof(width), 1, stdout);
    fwrite(&height, sizeof(height), 1, stdout);
    uint32_t outputFPS = (uint32_t)fps;
    fwrite(&outputFPS, sizeof(outputFPS), 1, stdout);

    CGImageRef image = first;
    while (1) {
        memset(buffer, 0, (size_t)height * bytesPerRow);
        CGContextSetInterpolationQuality(context, kCGInterpolationHigh);
        CGContextDrawImage(context, CGRectMake(0, 0, width, height), image);
        if (fwrite(buffer, 1, (size_t)height * bytesPerRow, stdout) != (size_t)height * bytesPerRow) break;
        CGImageRelease(image);
        usleep((useconds_t)(1000000 / fps));
        image = capture(createImage, windowID);
        if (!image) {
            windowID = find_window(required);
            if (windowID) image = capture(createImage, windowID);
        }
        if (!image) break;
    }
    if (image) CGImageRelease(image);
    CGContextRelease(context);
    free(buffer);
    return 0;
}
