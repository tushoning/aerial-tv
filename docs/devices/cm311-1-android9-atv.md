# CM311-1 / modified Android 9 ATV firmware

Reported by a user on 2026-10-21. These observations apply to this modified firmware, not every
CM311-1 or stock Android TV. The installed official build was Aerial 1.18 standard (versionCode 20).

Firmware properties read from the tested box:

- Android 9 / API 28, security patch level 2018-08-05.
- Build display ID: p291_iptv-userdebug 9 PPR1.180610.011 4.350135.00.21v1 test-keys.
- Incremental: 4.350135.00.21v1; board platform: p291_iptv.
- TV Settings: com.android.tv.settings versionName 1.0 / versionCode 1, targetSdk 28.
- The modified firmware reports the product model as S905L3; CM311-1 is the user's hardware name.

## Installation and HOME

- Android 9 / API 28, 32-bit ARM userspace (armeabi-v7a).
- Normal ADB installation succeeded. Android 9 has no role service; the documented
  set-home-activity fallback selected Aerial successfully.
- The previously installed third-party Dangbei launcher could bring itself back after HOME.
  After granting the documented permissions, Home Guard bound and HOME returned to Aerial.
- A reboot was tested: boot_completed was 1 and the foreground was Aerial without injecting
  HOME or manually starting it. Another existing accessibility service was preserved.
- The user still observed a brief Dangbei flash during boot, and explicitly requested disabling
  that third-party launcher. It was disabled, not uninstalled; HOME was checked again afterwards.
  A second reboot after disabling Dangbei was not tested. This is not a recommendation to disable
  manufacturer/system launchers on other TVs.
- APP_AUTO_START and APP_ASSOC_START are TCL-specific and unsupported here.
- The notification-listener grant command returned success, but Aerial was not added to the
  enabled listener setting. Do not infer working revival from the command's exit status.

## Right-side TV Settings panel shows black on the left

The user reports that opening system Settings from Aerial shows the settings panel on the right
but a black left side. The firmware's system component is
com.android.tv.settings/com.android.tv.settings.MainSettings.

On this same box, launching that component in a new task had previously produced the same issue
from another app. Launching it in the current task restored the original app behind the panel,
as confirmed visually by the user. That older app launched from a background service, so its
workaround needed a root task-launch helper. Aerial already has a foreground Activity and can
instead omit NEW_TASK; no root helper, hidden API or additional permission is proposed here.

The proposed compatibility path applies only when an Activity (including a wrapped Activity)
launches that exact system MainSettings component. It covers Control Center's settings route,
the picture-settings fallback and the system Settings app tile. Other OEM/full-screen settings
and non-Activity contexts keep their existing task flags.

**Verification boundary:** official 1.18 installation, HOME and one reboot were tested. The new
source patch has not been installed on this box: the user declined replacing the official signed
APK with a differently signed development build. The prior same-task visual test supports the
diagnosis but is not a device test of this patch. Maintainers should verify the patched settings
entry, Back returning to Aerial, and ordinary full-screen/OEM settings before release.
