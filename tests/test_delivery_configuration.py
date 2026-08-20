from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docker_runtime_matches_the_frozen_python_and_health_contract() -> None:
    api_dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in api_dockerfile
    assert "python:3.12" not in api_dockerfile
    assert "condition: service_healthy" in compose
    assert "mealpilot_runtime:/app/.runtime" in compose
    assert "mealpilot_internal" in compose


def test_web_image_and_proxy_preserve_spa_api_and_sse_behaviour() -> None:
    web_dockerfile = (PROJECT_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    nginx = (PROJECT_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    app = (PROJECT_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "pnpm install --frozen-lockfile" in web_dockerfile
    assert "location /v1/" in nginx
    assert "proxy_pass http://api:8000" in nginx
    assert "proxy_buffering off" in nginx
    assert "proxy_read_timeout 75s" in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "仅适用于健康成年人，不构成医疗建议" in app


def test_docker_contexts_exclude_local_dependencies_and_runtime_data() -> None:
    root_ignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    web_ignore = (PROJECT_ROOT / "frontend" / ".dockerignore").read_text(encoding="utf-8")

    assert root_ignore.startswith("**\n")
    assert "!backend/**" in root_ignore
    assert "!data/**" in root_ignore
    assert web_ignore.startswith("**\n")
    assert "!src/**" in web_ignore
    assert "node_modules" not in web_ignore  # deny-all means it remains excluded
    workspace = (PROJECT_ROOT / "frontend" / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    assert "packages:\n  - '.'" in workspace


def test_redesigned_frontend_preserves_safety_and_product_navigation() -> None:
    frontend = PROJECT_ROOT / "frontend" / "src"
    app = (frontend / "App.tsx").read_text(encoding="utf-8")
    shell = (frontend / "app" / "AppShell.tsx").read_text(encoding="utf-8")
    profile = (frontend / "context" / "UserProfileContext.tsx").read_text(encoding="utf-8")
    main = (frontend / "main.tsx").read_text(encoding="utf-8")

    assert "今日计划" in shell
    assert "对话助手" in shell
    assert "偏好与档案" in shell
    assert "仅适用于健康成年人，不构成医疗建议" in app
    assert "不能将空白视为安全" in profile
    assert 'allergenStatus === "unconfirmed"' in profile
    assert "phase2.css" not in main
    assert "styles/tokens.css" in main


def test_frontend_navigation_preserves_chat_and_drawer_focus() -> None:
    frontend = PROJECT_ROOT / "frontend" / "src"
    app = (frontend / "App.tsx").read_text(encoding="utf-8")
    drawer = (frontend / "features" / "profile" / "ProfileDrawer.tsx").read_text(encoding="utf-8")

    # Pages remain mounted while hidden, so in-flight chat and local messages survive navigation.
    assert 'hidden={activePage !== "assistant"}' in app
    assert 'activePage === "assistant" &&' not in app
    # Typing in the controlled profile fields must not rerun the focus-grabbing open effect.
    assert "const closeHandler = useRef(onClose)" in drawer
    assert "}, [open]);" in drawer
    assert "[open, onClose]" not in drawer
