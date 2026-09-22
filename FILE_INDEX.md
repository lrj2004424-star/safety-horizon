# File Index / 逐文件索引

路径为发布根目录相对路径。编号模块导航见 modules；不更名原 Python 导入模块。

| File | Purpose / 用途 |
|---|---|
| `.github/workflows/tests.yml` | Cross-platform CI / 两平台自动化验证 |
| `.gitignore` | Release source or metadata / 发布源码与元数据 |
| `.python-version` | Release source or metadata / 发布源码与元数据 |
| `01_install_macos.command` | Release source or metadata / 发布源码与元数据 |
| `02_run_standalone.command` | Release source or metadata / 发布源码与元数据 |
| `03_test.command` | Release source or metadata / 发布源码与元数据 |
| `LICENSE` | Release source or metadata / 发布源码与元数据 |
| `README.md` | Release source or metadata / 发布源码与元数据 |
| `RELEASE_NOTES.md` | Release source or metadata / 发布源码与元数据 |
| `SOURCE_PROVENANCE.json` | Release source or metadata / 发布源码与元数据 |
| `THIRD_PARTY_NOTICES.md` | Release source or metadata / 发布源码与元数据 |
| `VERSION` | Release source or metadata / 发布源码与元数据 |
| `docs/01_INSTALL.md` | Numbered deployment documentation / 部署文档 |
| `docs/02_RUNBOOK.md` | Numbered deployment documentation / 部署文档 |
| `docs/03_WORKFLOW_API.md` | Numbered deployment documentation / 部署文档 |
| `docs/04_TOUCHDESIGNER.md` | Numbered deployment documentation / 部署文档 |
| `docs/05_PRIVACY_SECURITY.md` | Numbered deployment documentation / 部署文档 |
| `docs/06_TROUBLESHOOTING.md` | Numbered deployment documentation / 部署文档 |
| `docs/07_FACTORY_ACCEPTANCE.md` | Numbered deployment documentation / 部署文档 |
| `docs/08_VALIDATION.md` | Numbered deployment documentation / 部署文档 |
| `docs/09_GITHUB_RELEASE.md` | Numbered deployment documentation / 部署文档 |
| `docs/10_PROJECT_STATEMENT.md` | Numbered deployment documentation / 部署文档 |
| `horizon.py` | Safety Horizon entry point / 独立于 TouchDesigner 的统一入口。 |
| `modules/01_capture/README.md` | Numbered module guide / 编号模块说明 |
| `modules/02_pose_risk/README.md` | Numbered module guide / 编号模块说明 |
| `modules/03_review/README.md` | Numbered module guide / 编号模块说明 |
| `modules/04_storage/README.md` | Numbered module guide / 编号模块说明 |
| `modules/05_feedback/README.md` | Numbered module guide / 编号模块说明 |
| `modules/06_actuation/README.md` | Numbered module guide / 编号模块说明 |
| `modules/07_touchdesigner/README.md` | Numbered module guide / 编号模块说明 |
| `platforms/macos/01_install.command` | macOS install/run/test guide / 苹果电脑入口 |
| `platforms/macos/02_run.command` | macOS install/run/test guide / 苹果电脑入口 |
| `platforms/macos/03_test.command` | macOS install/run/test guide / 苹果电脑入口 |
| `platforms/macos/README.md` | macOS install/run/test guide / 苹果电脑入口 |
| `platforms/windows/01_install.ps1` | Windows install/run/test guide / Windows 电脑入口 |
| `platforms/windows/02_run.cmd` | Windows install/run/test guide / Windows 电脑入口 |
| `platforms/windows/03_test.cmd` | Windows install/run/test guide / Windows 电脑入口 |
| `platforms/windows/README.md` | Windows install/run/test guide / Windows 电脑入口 |
| `release_tests/test_platforms.py` | Cross-platform checks / 平台互斥、采集路由、退出与隔离测试。 |
| `release_tests/test_portal.py` | Release-only integration tests / 发布接口回归；仅临时模拟数据，不触发硬件。 |
| `requirements-windows.lock` | Release source or metadata / 发布源码与元数据 |
| `requirements.in` | Release source or metadata / 发布源码与元数据 |
| `requirements.lock` | Release source or metadata / 发布源码与元数据 |
| `scripts/build_capture.sh` | Release source or metadata / 发布源码与元数据 |
| `scripts/package_release.py` | Allowlisted source packaging / 白名单打包，不打包现场数据或环境。 |
| `scripts/setup_assets.py` | Download official models with digest checks / 官方模型下载与完整性校验。 |
| `scripts/smoke_models.py` | Actual model inference on synthetic blank input / 真实加载模型，不使用员工素材。 |
| `scripts/smoke_video.py` | Actual model + local-video pipeline with synthetic non-person frames. |
| `scripts/verify_distribution.py` | Verify both platform ZIPs and run regression from the current-platform extracted copy. |
| `src/annotate.py` | Minimal keyboard video annotation tool for six research labels. |
| `src/app.py` | Run the joint hand, upper-body and machine-motion offline prototype. |
| `src/assets/sounds/01-yellow-attention-2s-68bpm.wav` | Generated diagnostic sound; not default alert / 生成的旧诊断音效 |
| `src/assets/sounds/02-orange-warning-4p5s-112bpm.wav` | Generated diagnostic sound; not default alert / 生成的旧诊断音效 |
| `src/assets/sounds/03-red-danger-9s-176bpm.wav` | Generated diagnostic sound; not default alert / 生成的旧诊断音效 |
| `src/assets/sounds/04-purple-grey-system-unavailable.wav` | Generated diagnostic sound; not default alert / 生成的旧诊断音效 |
| `src/calibrate.py` | Click warning, danger, machine-motion and reference polygons. |
| `src/calibrate_fixed_camera.py` | Capture a privacy-minimized, people-free reference for a fixed camera. |
| `src/calibrate_wide_layout.py` | Select one independent rectangular processing crop per wide-camera workstation. |
| `src/camera_probe.py` | List local camera indices that can return a frame without saving images. |
| `src/computer_buzzer_simulator.py` | Simulate the reviewed Arduino UNO buzzer on this Mac. |
| `src/config/ezviz-fatigue-multistation.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/ezviz-fatigue-single-upper.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/machine-state.example.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/station.example.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/video-21.fixed-8p5.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/video-21.offline.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/video32-lower.example.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/video32-upper.example.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/config/video32-wide.example.json` | Example / research configuration; recalibrate / 示例配置需现场标定 |
| `src/evaluate_offline.py` | Summarize one labeled offline run without claiming industrial accuracy. |
| `src/ezviz_monitor.py` | Friendly secure launcher for an EZVIZ/Hikvision NVR live channel. |
| `src/ezviz_multistation_fatigue_monitor.py` | Two independent whole-person fatigue observers from one visible EZVIZ window. |
| `src/ezviz_window_monitor.py` | Launch the configured whole-person fatigue observer on the EZVIZ window. |
| `src/factory_preflight.py` | Read-only deployment preflight. Never captures video or activates a buzzer. |
| `src/fatigue_window_monitor.py` | Observe whole-person fatigue-risk cues in an already-visible EZVIZ window. |
| `src/generate_alert_sounds.py` | Generate one heartbeat-like risk family at three tempos and durations. |
| `src/hardware/arduino_buzzer_basic_test/arduino_buzzer_basic_test.ino` | Arduino firmware / 板卡固件 |
| `src/hardware/arduino_status_indicator/arduino_status_indicator.ino` | Arduino firmware / 板卡固件 |
| `src/indicator_bridge.py` | Forward *review-authorized* advisory commands to an output-only Arduino. |
| `src/live_multicam.py` | Run the recommended fixed two-camera hand/pose/machine fusion prototype. |
| `src/macos_window_capture/EZVIZWindowCapture.swift` | Native macOS capture helper source / 原生采集源码 |
| `src/macos_window_capture/EZVIZWindowCaptureCG.c` | Native macOS capture helper source / 原生采集源码 |
| `src/macos_window_capture/ezviz_window_list.c` | Native macOS capture helper source / 原生采集源码 |
| `src/macos_window_capture/list_window_frames.swift` | Native macOS capture helper source / 原生采集源码 |
| `src/models/README.md` | Release source or metadata / 发布源码与元数据 |
| `src/owned_worker.py` | Wait for job ownership before running a child / 确认进程归属后启动服务。 |
| `src/safety_monitor/__init__.py` | Core package for the non-invasive cutting-station safety prototype. |
| `src/safety_monitor/audio_alerts.py` | Two-family audio feedback for the debounced joint safety state. |
| `src/safety_monitor/camera_device.py` | OpenCV camera device with bounded reconnect and health metadata. |
| `src/safety_monitor/capture_manager.py` | Opt-in, face-blurred snapshots and short event clips for the live monitor. |
| `src/safety_monitor/config.py` | Configuration loading and validation for one monitored workstation. |
| `src/safety_monitor/event_store.py` | Anonymous, metadata-only event log with local retention enforcement. |
| `src/safety_monitor/fatigue_engine.py` | Anonymous, temporal whole-person fatigue-risk proxy. |
| `src/safety_monitor/fatigue_status.py` | Atomic, image-free status output for the fatigue-risk monitor. |
| `src/safety_monitor/fatigue_visualization.py` | Readable overlay for anonymous whole-person fatigue-risk observation. |
| `src/safety_monitor/feature_store.py` | Frame-level anonymous feature export for labeling and lightweight baselines. |
| `src/safety_monitor/fixed_camera_guard.py` | Detect camera displacement against a people-free fixed-station reference. |
| `src/safety_monitor/frame_activity.py` | Conservative evidence of unchanged pixels, not proof of camera liveness. |
| `src/safety_monitor/geometry.py` | Small, dependency-free geometry helpers using normalized image coordinates. |
| `src/safety_monitor/hand_tracker.py` | MediaPipe Tasks hand-landmark adapter for OpenCV BGR frames. |
| `src/safety_monitor/input_router.py` | Hot-switch between the live EZVIZ window and labelled local test clips. |
| `src/safety_monitor/joint_engine.py` | Continuous hand, upper-body and machine-state fusion for the research MVP. |
| `src/safety_monitor/joint_visualization.py` | Overlay for the joint hand-pose-machine research prototype. |
| `src/safety_monitor/live_sources.py` | Explicit live-source selection / 明确选择窗口或 RTSP，不猜测输入。 |
| `src/safety_monitor/machine_motion.py` | Experimental optical-flow estimate for a calibrated machine motion ROI. |
| `src/safety_monitor/machine_truth.py` | Read-only machine-state evidence and conservative visual/truth fusion. |
| `src/safety_monitor/platform_runtime.py` | Cross-platform file locks and owned child processes / 跨平台互斥与进程回收。 |
| `src/safety_monitor/pose_tracker.py` | MediaPipe Pose Landmarker adapter and upper-body feature extraction. |
| `src/safety_monitor/pre_event_buffer.py` | Bounded, non-blocking pre/post-event review video buffer. |
| `src/safety_monitor/privacy.py` | Privacy transformations for derived demonstration outputs. |
| `src/safety_monitor/review_contract.py` | Validated data contracts for the safety-officer review workflow. |
| `src/safety_monitor/review_controller.py` | Application-facing API for the safety-officer review gate. |
| `src/safety_monitor/review_store.py` | Crash-resistant local queue and annotation storage for safety review. |
| `src/safety_monitor/risk_engine.py` | Temporal hand-to-hazard risk estimation with debounce and hysteresis. |
| `src/safety_monitor/serial_connection.py` | Bounded, nonblocking-retry serial output; never guesses among devices. |
| `src/safety_monitor/status_output.py` | Privacy-minimized live status snapshot for isolated display peripherals. |
| `src/safety_monitor/threshold_optimizer.py` | Conservative, review-driven risk-score threshold recommendations. |
| `src/safety_monitor/visualization.py` | OpenCV overlay for risk zones, detected landmarks, and advisory status. |
| `src/safety_monitor/wide_station.py` | Validated single-wide-camera layout for independent workstation pipelines. |
| `src/safety_monitor/window_capture_device.py` | Read a named macOS app window through native streaming or composited capture. |
| `src/safety_monitor/windows_capture.py` | Windows visible-window ROI capture / 仅采集用户选定窗口的视频区域。 |
| `src/safety_officer_review.py` | Safety-officer review sidecar for the TouchDesigner workflow. |
| `src/simulate.py` | Generate a four-state proof image without a camera or personal data. |
| `src/sound_check.py` | Explicitly audition one or all alert cues before a monitored session. |
| `src/start_touchdesigner_engine.command` | Release source or metadata / 发布源码与元数据 |
| `src/stream_probe.py` | Probe a live network stream without saving frames or printing credentials. |
| `src/supervise_service.py` | Own one worker, restart exited workers with bounded backoff, expose health. |
| `src/test_videos/README.md` | Release source or metadata / 发布源码与元数据 |
| `src/tests/test_audio_alerts.py` | Core regression / 核心模块测试 |
| `src/tests/test_camera_device.py` | Core regression / 核心模块测试 |
| `src/tests/test_capture_manager.py` | Core regression / 核心模块测试 |
| `src/tests/test_event_store.py` | Core regression / 核心模块测试 |
| `src/tests/test_fatigue_engine.py` | Core regression / 核心模块测试 |
| `src/tests/test_fixed_camera_guard.py` | Core regression / 核心模块测试 |
| `src/tests/test_geometry.py` | Core regression / 核心模块测试 |
| `src/tests/test_joint_engine.py` | Core regression / 核心模块测试 |
| `src/tests/test_lightweight_bridge.py` | Core regression / 核心模块测试 |
| `src/tests/test_machine_motion.py` | Core regression / 核心模块测试 |
| `src/tests/test_machine_truth.py` | Core regression / 核心模块测试 |
| `src/tests/test_multistation_fatigue.py` | Core regression / 核心模块测试 |
| `src/tests/test_pose_features.py` | Core regression / 核心模块测试 |
| `src/tests/test_pre_event_buffer.py` | Core regression / 核心模块测试 |
| `src/tests/test_recovery.py` | Core regression / 核心模块测试 |
| `src/tests/test_release_safety.py` | Release regressions: no physical outputs, all evidence in temporary dirs. |
| `src/tests/test_review_workflow.py` | Core regression / 核心模块测试 |
| `src/tests/test_risk_engine.py` | Core regression / 核心模块测试 |
| `src/tests/test_serial_connection.py` | Core regression / 核心模块测试 |
| `src/tests/test_status_output.py` | Core regression / 核心模块测试 |
| `src/tests/test_wide_station.py` | Core regression / 核心模块测试 |
| `src/tests/test_window_capture_device.py` | Core regression / 核心模块测试 |
| `src/touchdesigner/build_touchdesigner_project.py` | Create the 萤石安全视界 TouchDesigner版 .toe project. |
| `src/touchdesigner/td_runtime.py` | Runtime functions invoked by the TouchDesigner Execute DAT. |
| `src/wide_multistation.py` | Run independent safety pipelines for several crops from one fixed wide camera. |
| `ui/app.js` | Standalone local review interface / 独立本地审核界面 |
| `ui/index.html` | Standalone local review interface / 独立本地审核界面 |
| `ui/style.css` | Standalone local review interface / 独立本地审核界面 |
| `web_portal.py` | Loopback-only review UI; shared review service remains the sole actuator gate. |
