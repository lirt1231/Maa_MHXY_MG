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

extern "C" int MaaMHXYClickWindow(
    const char* bundle_id_utf8,
    const char* window_title_utf8,
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
        NSString* target_title = window_title_utf8
            ? [NSString stringWithUTF8String:window_title_utf8]
            : @"";
        NSArray<NSRunningApplication*>* applications =
            [NSRunningApplication runningApplicationsWithBundleIdentifier:bundle_id];
        if (applications.count == 0) {
            return -2;
        }

        CFArrayRef window_info_ref = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID);
        if (!window_info_ref) {
            return -3;
        }
        NSArray* window_info = CFBridgingRelease(window_info_ref);

        CGRect best_bounds = CGRectZero;
        NSRunningApplication* application = nil;
        double best_score = -1;
        for (NSDictionary* info in window_info) {
            NSNumber* owner_pid = info[(id)kCGWindowOwnerPID];
            NSNumber* layer = info[(id)kCGWindowLayer];
            NSRunningApplication* owner_application = nil;
            for (NSRunningApplication* candidate in applications) {
                if (!candidate.terminated
                    && candidate.processIdentifier == owner_pid.intValue) {
                    owner_application = candidate;
                    break;
                }
            }
            if (!owner_application || layer.intValue != 0) {
                continue;
            }

            CGRect bounds = CGRectZero;
            if (!read_window_bounds(info, &bounds) || bounds.size.width <= 0 || bounds.size.height <= 0) {
                continue;
            }

            NSString* title = info[(id)kCGWindowName];
            bool title_matches = target_title.length == 0 || [title isEqualToString:target_title];
            double area = bounds.size.width * bounds.size.height;
            double score = area + (title_matches ? 1e12 : 0);
            if (score > best_score) {
                best_score = score;
                best_bounds = bounds;
                application = owner_application;
            }
        }
        if (best_score < 0) {
            return -4;
        }

        [application activateWithOptions:NSApplicationActivateAllWindows];
        usleep(80000);

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
            usleep(50000);
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
    const char* bundle_id_utf8,
    const char* window_title_utf8,
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
        NSString* target_title = window_title_utf8
            ? [NSString stringWithUTF8String:window_title_utf8]
            : @"";
        NSArray<NSRunningApplication*>* applications =
            [NSRunningApplication runningApplicationsWithBundleIdentifier:bundle_id];
        if (applications.count == 0) {
            return -2;
        }

        CFArrayRef window_info_ref = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID);
        if (!window_info_ref) {
            return -3;
        }
        NSArray* window_info = CFBridgingRelease(window_info_ref);
        CGRect best_bounds = CGRectZero;
        NSRunningApplication* application = nil;
        double best_score = -1;
        for (NSDictionary* info in window_info) {
            NSNumber* owner_pid = info[(id)kCGWindowOwnerPID];
            NSNumber* layer = info[(id)kCGWindowLayer];
            NSRunningApplication* owner_application = nil;
            for (NSRunningApplication* candidate in applications) {
                if (!candidate.terminated
                    && candidate.processIdentifier == owner_pid.intValue) {
                    owner_application = candidate;
                    break;
                }
            }
            if (!owner_application || layer.intValue != 0) {
                continue;
            }
            CGRect bounds = CGRectZero;
            if (!read_window_bounds(info, &bounds) || bounds.size.width <= 0 || bounds.size.height <= 0) {
                continue;
            }
            NSString* title = info[(id)kCGWindowName];
            bool title_matches = target_title.length == 0 || [title isEqualToString:target_title];
            double area = bounds.size.width * bounds.size.height;
            double score = area + (title_matches ? 1e12 : 0);
            if (score > best_score) {
                best_score = score;
                best_bounds = bounds;
                application = owner_application;
            }
        }
        if (best_score < 0) {
            return -4;
        }

        [application activateWithOptions:NSApplicationActivateAllWindows];
        usleep(80000);

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
