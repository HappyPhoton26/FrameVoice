// vrmic - the VR side of voicevr. Two overlays, nothing rendered per frame:
//   * the voice button: a small mic button on the back of the left wrist (follows the left controller),
//     always showing. Point the other hand's laser at it and click: prints "tap" (voicevr then listens
//     for one phrase). Hold the trigger on it instead to talk for as long as you hold: "down"/"up".
//     It turns amber while voicevr is thinking.
//   * standby: the button shrinks to a small grey dot and clicks are checked less often; clicking it prints
//     "wake" and it turns amber until voicevr says it's ready again.
//   * the status icon: bottom centre of your view (fixed to the headset), shown only while voicevr is
//     listening or working.
//   stdin from voicevr: "icon <listening|thinking|ok|fail>" shows a state (ok/fail go back to idle
//     after a moment), "hide" goes back to idle, "button <button|button-off>" sets the button's idle image,
//     "mode <active|standby|waking>" switches standby.
// Usage: vrmic <icon dir> [x y z of the status icon from the headset, m] [width m]
//              [x y z of the button from the left controller, m] [tilt deg] [button width m]
#include <openvr/openvr_capi.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

extern intptr_t VR_InitInternal(EVRInitError *peError, EVRApplicationType eType);
extern void VR_ShutdownInternal(void);
extern intptr_t VR_GetGenericInterface(const char *pchInterfaceVersion, EVRInitError *peError);

static volatile int g_quit;
static void on_sig(int s) { (void)s; g_quit = 1; }
static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

static struct VR_IVROverlay_FnTable *ov;
static char icon_dir[1800];


static void set_image(VROverlayHandle_t h, const char *name) {
    char path[2048]; snprintf(path, sizeof path, "%s/%s.png", icon_dir, name);
    ov->SetOverlayFromFile(h, path);
}

static VROverlayHandle_t make_overlay(const char *what, const char *name, float width) {
    VROverlayHandle_t h = 0;
    char key[64]; snprintf(key, sizeof key, "voicevr.%s.%d", what, (int)getpid());
    if (ov->CreateOverlay(key, name, &h)) return 0;
    ov->SetOverlayWidthInMeters(h, width);
    ov->SetOverlayInputMethod(h, VROverlayInputMethod_Mouse);
    ov->SetOverlayFlag(h, VROverlayFlags_MakeOverlaysInteractiveIfVisible, 1);
    struct HmdVector2_t ms = {{1, 1}};
    ov->SetOverlayMouseScale(h, &ms);
    return h;
}

int main(int argc, char **argv) {
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);
    setvbuf(stdout, NULL, _IOLBF, 0);
    snprintf(icon_dir, sizeof icon_dir, "%s", argc > 1 ? argv[1] : ".");
    #define ARG(i, def) (argc > (i) ? (float)atof(argv[i]) : (def))
    float ox = ARG(2, 0.0f), oy = ARG(3, -0.17f), oz = ARG(4, -0.6f), width = ARG(5, 0.026f);
    float bx = ARG(6, 0.0f), by = ARG(7, 0.02f), bz = ARG(8, 0.13f), tilt = ARG(9, -60.0f), bwidth = ARG(10, 0.04f);

    EVRInitError err = EVRInitError_VRInitError_None;
    VR_InitInternal(&err, EVRApplicationType_VRApplication_Overlay);
    if (err) { fprintf(stderr, "VR_Init failed: %d\n", err); return 1; }
    ov = (void *)VR_GetGenericInterface("FnTable:IVROverlay_027", &err);
    struct VR_IVRSystem_FnTable *vsys = (void *)VR_GetGenericInterface("FnTable:IVRSystem_022", &err);
    if (!ov || !vsys) { fprintf(stderr, "OpenVR interfaces missing\n"); return 1; }

    VROverlayHandle_t mic = make_overlay("mic", "Voice status", width);
    VROverlayHandle_t btn = make_overlay("button", "Voice button", bwidth);
    if (!mic || !btn) { fprintf(stderr, "overlays unavailable\n"); return 1; }
    struct HmdMatrix34_t m = {{{1, 0, 0, ox}, {0, 1, 0, oy}, {0, 0, 1, oz}}};
    ov->SetOverlayTransformTrackedDeviceRelative(mic, k_unTrackedDeviceIndex_Hmd, &m);
    // button: tilted about the controller's x axis so it faces up and back toward your eyes
    float c = cosf(tilt * (float)M_PI / 180), s = sinf(tilt * (float)M_PI / 180);
    struct HmdMatrix34_t bm = {{{1, 0, 0, bx}, {0, c, -s, by}, {0, s, c, bz}}};
    TrackedDeviceIndex_t left = k_unTrackedDeviceIndexInvalid;

    char idle[64] = "button";            // button (wake word on) / button-off
    char mode[16] = "active";            // active / standby (small grey dot) / waking (amber, ignores clicks)
    // status icon hidden while idle: it only appears while listening or working on a command
    #define SHOW(name, alpha) do { set_image(mic, name); ov->SetOverlayAlpha(mic, alpha); ov->ShowOverlay(mic); } while (0)
    #define BTN(name) set_image(btn, !strcmp(mode, "standby") ? "button-off" : !strcmp(mode, "waking") ? "thinking" : (name))
    #define IDLE() do { ov->HideOverlay(mic); BTN(idle); } while (0)
    IDLE();

    fcntl(0, F_SETFL, fcntl(0, F_GETFL) | O_NONBLOCK);
    char cmd[256]; int cmd_n = 0, held = 0;
    double held_since = 0, hold_start = 0, idle_at = 0, find_at = 0;
    while (!g_quit) {
        if (now_s() > find_at) {   // follow the left controller (it can disconnect / come back)
            find_at = now_s() + 1;
            TrackedDeviceIndex_t l = vsys->GetTrackedDeviceIndexForControllerRole(ETrackedControllerRole_TrackedControllerRole_LeftHand);
            if (l != left) {
                left = l;
                if (left == k_unTrackedDeviceIndexInvalid) ov->HideOverlay(btn);
                else { ov->SetOverlayTransformTrackedDeviceRelative(btn, left, &bm); ov->ShowOverlay(btn); }
            }
        }
        struct VREvent_t ev;
        while (vsys->PollNextEvent(&ev, sizeof ev)) {}
        while (ov->PollNextOverlayEvent(mic, &ev, sizeof ev)) {}
        while (ov->PollNextOverlayEvent(btn, &ev, sizeof ev)) {
            if (strcmp(mode, "active")) {   // standby: any click wakes voicevr up; waking: wait
                if (ev.eventType == EVREventType_VREvent_MouseButtonDown && !strcmp(mode, "standby")) {
                    snprintf(mode, sizeof mode, "waking");
                    ov->SetOverlayWidthInMeters(btn, bwidth); ov->SetOverlayAlpha(btn, 1.0f); BTN(idle);
                    printf("wake\n");
                }
                continue;
            }
            if (ev.eventType == EVREventType_VREvent_MouseButtonDown && !held) {
                held = 1; held_since = now_s(); idle_at = 0;
                SHOW("listening", 0.9f); BTN("listening");
            } else if (ev.eventType == EVREventType_VREvent_MouseButtonUp && held) {
                held = 0;
                if (held_since == 0) printf("up\n");                     // was a hold: stop talking
                else printf("tap\n");                                    // quick click: listen for a phrase
            }
        }
        // held longer than a click: push to talk until the trigger is let go
        if (held && held_since && now_s() - held_since > 0.6) { held_since = 0; hold_start = now_s(); printf("down\n"); }
        if (held && !held_since && now_s() - hold_start > 30) { held = 0; printf("up\n"); }   // safety
        char ch; ssize_t r;
        while ((r = read(0, &ch, 1)) == 1) {
            if (ch != '\n') { if (cmd_n < (int)sizeof cmd - 1) cmd[cmd_n++] = ch; continue; }
            cmd[cmd_n] = 0; cmd_n = 0;
            if (!strncmp(cmd, "icon ", 5)) {
                SHOW(cmd + 5, 0.9f);
                const char *b = cmd + 5;
                BTN(!strcmp(b, "listening") || !strcmp(b, "thinking") ? b : idle);
                idle_at = (!strcmp(b, "ok") || !strcmp(b, "fail")) ? now_s() + 1.2 : 0;
            } else if (!strcmp(cmd, "hide")) { if (!held) IDLE(); idle_at = 0; }
            else if (!strncmp(cmd, "mode ", 5)) {
                snprintf(mode, sizeof mode, "%.15s", cmd + 5);
                int sb = !strcmp(mode, "standby");
                if (sb) held = 0;
                ov->SetOverlayWidthInMeters(btn, sb ? bwidth * 0.4f : bwidth);
                ov->SetOverlayAlpha(btn, sb ? 0.6f : 1.0f);
                IDLE(); idle_at = 0;
            }
            else if (!strncmp(cmd, "button ", 7)) {
                snprintf(idle, sizeof idle, "%.60s", cmd + 7);
                if (!held && !idle_at && !ov->IsOverlayVisible(mic)) BTN(idle);
            }
        }
        if (r == 0) break;   // voicevr went away
        if (idle_at && now_s() > idle_at) { IDLE(); idle_at = 0; }
        usleep(strcmp(mode, "standby") ? 20000 : 100000);   // standby: check 10x a second instead of 50x
    }
    VR_ShutdownInternal();
    return 0;
}
