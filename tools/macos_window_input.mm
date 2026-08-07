#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>

#include <algorithm>
#include <cmath>
#include <unistd.h>

namespace {

bool read_window_bounds(NSDictionary* info, CGRect* bounds)
{
    NSDictionary* dictionary = info[(id)kCGWindowBounds];
    if (![dictionary isKindOfClass:[NSDictionary class]]) {
        return false;
    }
    return CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)dictionary, bounds);
}

int resolve_target_window(
    uint32_t window_id,
    NSString* bundle_id,
    CGRect* bounds,
    pid_t* owner_pid)
{
    if (window_id == 0 || bundle_id.length == 0) {
        return -1;
    }
    CFArrayRef window_info_ref = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID);
    if (!window_info_ref) {
        return -3;
    }
    NSArray* window_info = CFBridgingRelease(window_info_ref);
    for (NSDictionary* info in window_info) {
        NSNumber* number = info[(id)kCGWindowNumber];
        NSNumber* layer = info[(id)kCGWindowLayer];
        if (number.unsignedIntValue != window_id || layer.intValue != 0) {
            continue;
        }
        CGRect candidate_bounds = CGRectZero;
        if (!read_window_bounds(info, &candidate_bounds)
            || candidate_bounds.size.width <= 0
            || candidate_bounds.size.height <= 0) {
            return -4;
        }
        pid_t candidate_pid = [info[(id)kCGWindowOwnerPID] intValue];
        NSRunningApplication* application =
            [NSRunningApplication runningApplicationWithProcessIdentifier:candidate_pid];
        if (!application
            || application.terminated
            || ![application.bundleIdentifier isEqualToString:bundle_id]) {
            return -4;
        }
        *bounds = candidate_bounds;
        *owner_pid = candidate_pid;
        return 0;
    }
    return -4;
}

bool ax_window_matches_bounds(AXUIElementRef window_element, CGRect target_bounds)
{
    CFTypeRef position_value = nullptr;
    CFTypeRef size_value = nullptr;
    CGPoint position = CGPointZero;
    CGSize size = CGSizeZero;
    bool matches = false;
    if (AXUIElementCopyAttributeValue(
            window_element, kAXPositionAttribute, &position_value) == kAXErrorSuccess
        && AXUIElementCopyAttributeValue(
            window_element, kAXSizeAttribute, &size_value) == kAXErrorSuccess
        && position_value && size_value
        && CFGetTypeID(position_value) == AXValueGetTypeID()
        && CFGetTypeID(size_value) == AXValueGetTypeID()
        && AXValueGetValue(
            (AXValueRef)position_value, (AXValueType)kAXValueCGPointType, &position)
        && AXValueGetValue(
            (AXValueRef)size_value, (AXValueType)kAXValueCGSizeType, &size)) {
        constexpr double tolerance = 2.0;
        matches = std::abs(position.x - target_bounds.origin.x) <= tolerance
            && std::abs(position.y - target_bounds.origin.y) <= tolerance
            && std::abs(size.width - target_bounds.size.width) <= tolerance
            && std::abs(size.height - target_bounds.size.height) <= tolerance;
    }
    if (position_value) CFRelease(position_value);
    if (size_value) CFRelease(size_value);
    return matches;
}

int focus_target_window(pid_t owner_pid, CGRect target_bounds)
{
    AXUIElementRef application_element = AXUIElementCreateApplication(owner_pid);
    if (!application_element) {
        return -7;
    }
    AXError frontmost_result = AXUIElementSetAttributeValue(
        application_element, kAXFrontmostAttribute, kCFBooleanTrue);
    AXError raise_result = kAXErrorFailure;
    CFTypeRef windows_value = nullptr;
    if (AXUIElementCopyAttributeValue(
            application_element, kAXWindowsAttribute, &windows_value) == kAXErrorSuccess
        && windows_value
        && CFGetTypeID(windows_value) == CFArrayGetTypeID()) {
        CFArrayRef windows = (CFArrayRef)windows_value;
        for (CFIndex index = 0; index < CFArrayGetCount(windows); ++index) {
            AXUIElementRef window_element = (AXUIElementRef)CFArrayGetValueAtIndex(
                windows, index);
            if (ax_window_matches_bounds(window_element, target_bounds)) {
                raise_result = AXUIElementPerformAction(window_element, kAXRaiseAction);
                break;
            }
        }
    }
    if (windows_value) {
        CFRelease(windows_value);
    }
    CFRelease(application_element);

    if (frontmost_result != kAXErrorSuccess && raise_result != kAXErrorSuccess) {
        return -7;
    }
    NSRunningApplication* application =
        [NSRunningApplication runningApplicationWithProcessIdentifier:owner_pid];
    if (!application || application.terminated) {
        return -7;
    }
    [application activateWithOptions:0];
    bool frontmost_confirmed = false;
    for (int attempt = 0; attempt < 10; ++attempt) {
        NSRunningApplication* frontmost =
            NSWorkspace.sharedWorkspace.frontmostApplication;
        if (frontmost && frontmost.processIdentifier == owner_pid) {
            frontmost_confirmed = true;
            break;
        }
        // The iOS-wrapped client occasionally drops very short synthetic
        // taps on purchase controls. Keep a single balanced click, but hold
        // it long enough for the client to register the touch.
        usleep(180000);
    }
    if (!frontmost_confirmed) {
        return -7;
    }
    usleep(100000);
    return 0;
}

} // namespace

extern "C" uint32_t MaaMHXYFindWindow(const char* bundle_id_utf8, const char* window_title_utf8)
{
    @autoreleasepool {
        if (!bundle_id_utf8) {
            return 0;
        }
        NSString* bundle_id = [NSString stringWithUTF8String:bundle_id_utf8];
        NSString* target_title = window_title_utf8
            ? [NSString stringWithUTF8String:window_title_utf8]
            : @"";
        NSArray<NSRunningApplication*>* applications =
            [NSRunningApplication runningApplicationsWithBundleIdentifier:bundle_id];
        NSMutableSet<NSNumber*>* process_ids = [NSMutableSet set];
        for (NSRunningApplication* candidate in applications) {
            if (!candidate.terminated) {
                [process_ids addObject:@(candidate.processIdentifier)];
            }
        }
        if (process_ids.count == 0) {
            return 0;
        }

        CFArrayRef window_info_ref = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID);
        if (!window_info_ref) {
            return 0;
        }
        NSArray* window_info = CFBridgingRelease(window_info_ref);
        uint32_t best_window_id = 0;
        double best_score = -1;
        for (NSDictionary* info in window_info) {
            NSNumber* owner_pid = info[(id)kCGWindowOwnerPID];
            NSNumber* layer = info[(id)kCGWindowLayer];
            if (![process_ids containsObject:owner_pid] || layer.intValue != 0) {
                continue;
            }
            CGRect bounds = CGRectZero;
            if (!read_window_bounds(info, &bounds) || bounds.size.width <= 0 || bounds.size.height <= 0) {
                continue;
            }
            NSString* title = info[(id)kCGWindowName];
            bool title_matches = target_title.length == 0 || [title isEqualToString:target_title];
            double score = bounds.size.width * bounds.size.height + (title_matches ? 1e12 : 0);
            if (score > best_score) {
                best_score = score;
                best_window_id = [info[(id)kCGWindowNumber] unsignedIntValue];
            }
        }
        return best_window_id;
    }
}

extern "C" int MaaMHXYGetWindowGeometry(
    uint32_t window_id,
    const char* bundle_id_utf8,
    double* output_x,
    double* output_y,
    double* output_width,
    double* output_height,
    int* output_pid)
{
    @autoreleasepool {
        if (!bundle_id_utf8) {
            return -1;
        }
        NSString* bundle_id = [NSString stringWithUTF8String:bundle_id_utf8];
        CGRect bounds = CGRectZero;
        pid_t owner_pid = 0;
        int result = resolve_target_window(window_id, bundle_id, &bounds, &owner_pid);
        if (result != 0) {
            return result;
        }
        if (output_x) *output_x = bounds.origin.x;
        if (output_y) *output_y = bounds.origin.y;
        if (output_width) *output_width = bounds.size.width;
        if (output_height) *output_height = bounds.size.height;
        if (output_pid) *output_pid = owner_pid;
        return 0;
    }
}

extern "C" int MaaMHXYClickScreenPoint(
    uint32_t window_id,
    const char* bundle_id_utf8,
    double screen_x,
    double screen_y,
    double* output_x,
    double* output_y)
{
    @autoreleasepool {
        if (!bundle_id_utf8 || !std::isfinite(screen_x) || !std::isfinite(screen_y)) {
            return -1;
        }
        NSString* bundle_id = [NSString stringWithUTF8String:bundle_id_utf8];
        CGRect bounds = CGRectZero;
        pid_t owner_pid = 0;
        int result = resolve_target_window(window_id, bundle_id, &bounds, &owner_pid);
        if (result != 0) {
            return result;
        }
        CGPoint point = CGPointMake(screen_x, screen_y);
        if (!CGRectContainsPoint(bounds, point)) {
            return -6;
        }
        int focus_result = focus_target_window(owner_pid, bounds);
        if (focus_result != 0) {
            return focus_result;
        }

        if (output_x) *output_x = point.x;
        if (output_y) *output_y = point.y;
        CGEventRef current_event = CGEventCreate(nullptr);
        bool restore_cursor = current_event != nullptr;
        CGPoint saved_position = current_event ? CGEventGetLocation(current_event) : CGPointZero;
        if (current_event) CFRelease(current_event);

        CGEventRef mouse_move = CGEventCreateMouseEvent(
            nullptr, kCGEventMouseMoved, point, kCGMouseButtonLeft);
        CGEventRef mouse_down = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseDown, point, kCGMouseButtonLeft);
        CGEventRef mouse_up = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseUp, point, kCGMouseButtonLeft);
        if (!mouse_move || !mouse_down || !mouse_up) {
            if (mouse_move) CFRelease(mouse_move);
            if (mouse_down) CFRelease(mouse_down);
            if (mouse_up) CFRelease(mouse_up);
            return -5;
        }
        CGEventSetIntegerValueField(mouse_down, kCGMouseEventClickState, 1);
        CGEventSetIntegerValueField(mouse_up, kCGMouseEventClickState, 1);
        CGEventPost(kCGHIDEventTap, mouse_move);
        CGEventPost(kCGHIDEventTap, mouse_down);
        usleep(50000);
        CGEventPost(kCGHIDEventTap, mouse_up);
        CFRelease(mouse_move);
        CFRelease(mouse_down);
        CFRelease(mouse_up);
        usleep(180000);
        if (restore_cursor) {
            CGWarpMouseCursorPosition(saved_position);
        }
        return 0;
    }
}

extern "C" int MaaMHXYDragScreenPoints(
    uint32_t window_id,
    const char* bundle_id_utf8,
    double from_x,
    double from_y,
    double to_x,
    double to_y)
{
    @autoreleasepool {
        if (!bundle_id_utf8
            || !std::isfinite(from_x) || !std::isfinite(from_y)
            || !std::isfinite(to_x) || !std::isfinite(to_y)) {
            return -1;
        }
        NSString* bundle_id = [NSString stringWithUTF8String:bundle_id_utf8];
        CGRect bounds = CGRectZero;
        pid_t owner_pid = 0;
        int result = resolve_target_window(window_id, bundle_id, &bounds, &owner_pid);
        if (result != 0) {
            return result;
        }
        CGPoint from = CGPointMake(from_x, from_y);
        CGPoint to = CGPointMake(to_x, to_y);
        if (!CGRectContainsPoint(bounds, from) || !CGRectContainsPoint(bounds, to)) {
            return -6;
        }
        int focus_result = focus_target_window(owner_pid, bounds);
        if (focus_result != 0) {
            return focus_result;
        }

        CGEventRef mouse_down = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseDown, from, kCGMouseButtonLeft);
        CGEventRef mouse_drag = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseDragged, to, kCGMouseButtonLeft);
        CGEventRef mouse_up = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseUp, to, kCGMouseButtonLeft);
        if (!mouse_down || !mouse_drag || !mouse_up) {
            if (mouse_down) CFRelease(mouse_down);
            if (mouse_drag) CFRelease(mouse_drag);
            if (mouse_up) CFRelease(mouse_up);
            return -5;
        }
        CGEventPost(kCGHIDEventTap, mouse_down);
        usleep(80000);
        CGEventPost(kCGHIDEventTap, mouse_drag);
        usleep(180000);
        CGEventPost(kCGHIDEventTap, mouse_up);
        CFRelease(mouse_down);
        CFRelease(mouse_drag);
        CFRelease(mouse_up);
        return 0;
    }
}

extern "C" int MaaMHXYClickWindow(
    uint32_t window_id,
    const char* bundle_id_utf8,
    double source_x,
    double source_y,
    double source_width,
    double source_height,
    double* output_x,
    double* output_y)
{
    @autoreleasepool {
        if (!bundle_id_utf8 || source_width <= 0 || source_height <= 0) {
            return -1;
        }

        NSString* bundle_id = [NSString stringWithUTF8String:bundle_id_utf8];
        CGRect best_bounds = CGRectZero;
        pid_t owner_pid = 0;
        int resolve_result = resolve_target_window(
            window_id, bundle_id, &best_bounds, &owner_pid);
        if (resolve_result != 0) {
            return resolve_result;
        }
        int focus_result = focus_target_window(owner_pid, best_bounds);
        if (focus_result != 0) {
            return focus_result;
        }

        double normalized_x = std::clamp(source_x / source_width, 0.0, 1.0);
        double normalized_y = std::clamp(source_y / source_height, 0.0, 1.0);
        // ScreenCaptureKit gives Maa the game content without the standard
        // macOS title bar, while CGWindow bounds include it.
        constexpr double title_bar_height = 28.0;
        double content_height = std::max(1.0, best_bounds.size.height - title_bar_height);
        CGPoint point = CGPointMake(
            best_bounds.origin.x + normalized_x * best_bounds.size.width,
            best_bounds.origin.y + title_bar_height + normalized_y * content_height);
        if (output_x) {
            *output_x = point.x;
        }
        if (output_y) {
            *output_y = point.y;
        }

        CGEventRef current_event = CGEventCreate(nullptr);
        CGPoint saved_position = current_event ? CGEventGetLocation(current_event) : CGPointZero;
        if (current_event) {
            CFRelease(current_event);
        }

        // Activation happens before the input is posted. Always submit exactly
        // one balanced down/up pair so a stop request cannot leave a hidden
        // second click queued after the Python runner has acknowledged it.
        for (int attempt = 0; attempt < 1; ++attempt) {
            CGEventRef mouse_move = CGEventCreateMouseEvent(
                nullptr, kCGEventMouseMoved, point, kCGMouseButtonLeft);
            CGEventRef mouse_down = CGEventCreateMouseEvent(
                nullptr, kCGEventLeftMouseDown, point, kCGMouseButtonLeft);
            CGEventRef mouse_up = CGEventCreateMouseEvent(
                nullptr, kCGEventLeftMouseUp, point, kCGMouseButtonLeft);
            if (!mouse_move || !mouse_down || !mouse_up) {
                if (mouse_move) {
                    CFRelease(mouse_move);
                }
                if (mouse_down) {
                    CFRelease(mouse_down);
                }
                if (mouse_up) {
                    CFRelease(mouse_up);
                }
                return -5;
            }

            CGEventSetIntegerValueField(mouse_down, kCGMouseEventClickState, 1);
            CGEventSetIntegerValueField(mouse_up, kCGMouseEventClickState, 1);
            CGEventPost(kCGHIDEventTap, mouse_move);
            CGEventPost(kCGHIDEventTap, mouse_down);
            // See MaaMHXYClickScreenPoint: retain a single click while using
            // a touch-like press duration for the wrapped client.
            usleep(180000);
            CGEventPost(kCGHIDEventTap, mouse_up);
            CFRelease(mouse_move);
            CFRelease(mouse_down);
            CFRelease(mouse_up);
            usleep(180000);
        }
        if (current_event) {
            CGWarpMouseCursorPosition(saved_position);
        }
        return 0;
    }
}

extern "C" int MaaMHXYDragWindow(
    uint32_t window_id,
    const char* bundle_id_utf8,
    double source_from_x,
    double source_from_y,
    double source_to_x,
    double source_to_y,
    double source_width,
    double source_height)
{
    @autoreleasepool {
        if (!bundle_id_utf8 || source_width <= 0 || source_height <= 0) {
            return -1;
        }
        NSString* bundle_id = [NSString stringWithUTF8String:bundle_id_utf8];
        CGRect best_bounds = CGRectZero;
        pid_t owner_pid = 0;
        int resolve_result = resolve_target_window(
            window_id, bundle_id, &best_bounds, &owner_pid);
        if (resolve_result != 0) {
            return resolve_result;
        }
        int focus_result = focus_target_window(owner_pid, best_bounds);
        if (focus_result != 0) {
            return focus_result;
        }

        auto map_point = [&](double x, double y) {
            constexpr double title_bar_height = 28.0;
            double content_height = std::max(1.0, best_bounds.size.height - title_bar_height);
            return CGPointMake(
                best_bounds.origin.x + std::clamp(x / source_width, 0.0, 1.0) * best_bounds.size.width,
                best_bounds.origin.y + title_bar_height
                    + std::clamp(y / source_height, 0.0, 1.0) * content_height);
        };
        CGPoint from = map_point(source_from_x, source_from_y);
        CGPoint to = map_point(source_to_x, source_to_y);
        CGEventRef mouse_down = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseDown, from, kCGMouseButtonLeft);
        CGEventRef mouse_drag = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseDragged, to, kCGMouseButtonLeft);
        CGEventRef mouse_up = CGEventCreateMouseEvent(
            nullptr, kCGEventLeftMouseUp, to, kCGMouseButtonLeft);
        if (!mouse_down || !mouse_drag || !mouse_up) {
            if (mouse_down) CFRelease(mouse_down);
            if (mouse_drag) CFRelease(mouse_drag);
            if (mouse_up) CFRelease(mouse_up);
            return -5;
        }
        CGEventPost(kCGHIDEventTap, mouse_down);
        usleep(80000);
        CGEventPost(kCGHIDEventTap, mouse_drag);
        usleep(180000);
        CGEventPost(kCGHIDEventTap, mouse_up);
        CFRelease(mouse_down);
        CFRelease(mouse_drag);
        CFRelease(mouse_up);
        return 0;
    }
}
