# OpenClaw 版本文件清单

以下文件属于默认“仓库版本落点”，`openclaw-version-bump` 会默认管理其中的 short version 字段。

- `package.json`
  - `version`
- `apps/android/app/build.gradle.kts`
  - `versionName`
  - `versionCode`
- `apps/ios/Sources/Info.plist`
  - `CFBundleShortVersionString`
  - `CFBundleVersion`
- `apps/ios/Tests/Info.plist`
  - `CFBundleShortVersionString`
  - `CFBundleVersion`
- `apps/macos/Sources/OpenClaw/Resources/Info.plist`
  - `CFBundleShortVersionString`
  - `CFBundleVersion`

不在本 skill 范围内（可手工同步）：

- `docs/install/updating.md` 示例文案
- `docs/platforms/mac/release.md` 示例文案
- `appcast.xml`
