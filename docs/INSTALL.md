# Windows 설치

Windows x64, 64비트 Python 3.11 이상, 본인 계정으로 로그인되어 실행 중인 PC 카카오톡이 필요합니다. 아래 소스 설치에는 Git이 필요하고, 최초 의존성 설치에는 인터넷이 필요합니다. 로컬 MCP 등록을 선택하면 Codex CLI도 필요합니다. 아직 PyPI나 설치 EXE를 배포하지 않았습니다.

```powershell
git clone https://github.com/wansucong-stoom/stoom-openkkt.git
cd stoom-openkkt
./scripts/install.ps1
```

다른 Python은 `-Python 'C:/path/to/python.exe'`로 지정합니다. 설치는 프로젝트 가상환경만 사용하고 기존 개인 설정을 덮어쓰지 않습니다. PowerShell 정책 때문에 스크립트를 실행할 수 없다면 시스템 정책을 변경하지 않고 아래 수동 방법을 사용합니다.

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e '.[mcp]'
Copy-Item -LiteralPath examples/config.example.json -Destination config.local.json
```

## 개인 설정

```json
{
  "profile": "C:/Users/YOUR_NAME/AppData/Local/Kakao/KakaoTalk/users/YOUR_PROFILE",
  "store": "./data/assistant.sqlite",
  "executable": "C:/Program Files/Kakao/KakaoTalk/KakaoTalk.exe"
}
```

`profile`은 `%LOCALAPPDATA%/Kakao/KakaoTalk/users` 아래에서 `chatfolder.edb`와 `chat_data`가 있는 본인 계정의 폴더입니다. GUI의 **데이터 폴더 찾기**로 후보를 표시한 뒤 사용할 계정을 직접 선택합니다. 여러 계정이 있어도 임의로 선택하거나 대화를 읽지 않습니다.

`store`는 대화를 보관할 별도 SQLite 파일이며 원본 프로필 내부에 둘 수 없습니다. 상대 경로는 설정 파일 위치를 기준으로 해석합니다. 개인 설정과 DB는 저장소 밖에 둬도 됩니다. 키·암호·access token은 설정에 넣지 않습니다.

`executable`은 실제 설치 경로입니다. 같은 이름의 다른 설치·Sandboxie 프로세스는 선택하지 않습니다. 기존 명시 PID 방식은 `executable` 대신 `"pids": [12345]`로 사용할 수 있으며 재시작 후 갱신해야 합니다.

## 첫 조회와 중지

설정을 화면에서 하려면 `./.venv/Scripts/openkkt.exe gui`를 실행합니다. GUI는 이 PC의 127.0.0.1에서만 열리며 외부 라이브러리·분석·업로드를 사용하지 않습니다. 데이터 폴더 후보는 파일 이름만 확인하며, 사용자가 선택하고 저장한 뒤에 범주를 읽습니다. 대화 조회는 별도 버튼으로 요청합니다.

일반 STDIO MCP에는 네트워크 주소가 필요하지 않습니다. 브라우저 탭을 닫아도 GUI 서버는 실행 중입니다. 터미널에서 실행했다면 Ctrl+C로 서버를 종료합니다.

```powershell
./.venv/Scripts/openkkt.exe folders
./.venv/Scripts/openkkt.exe scope '업무' '프로젝트'
./.venv/Scripts/openkkt.exe read --recent --limit 50
./.venv/Scripts/openkkt.exe read --text '마감' --limit 20
./.venv/Scripts/openkkt.exe scope
```

범주는 가상 예시입니다. 목록에서 실제 사용자 생성 범주를 선택합니다. `scope`는 현재 선택을 완전히 교체합니다. 인자를 비우면 수집 범위를 비우고 제외된 방의 활성 데이터를 제거합니다. 기본 선택은 비어 있어 지정 전에는 본문을 수집하지 않습니다.

## 선택 사항과 업데이트

`watch --interval 30`은 터미널에서 주기적 수집을 하며 Ctrl+C로 중지합니다. 자동 시작·서비스·예약 작업을 설치하지 않습니다. 일반 dot 조회는 `read` 또는 `serve --live`로 호출 시 갱신합니다.

업데이트는 `git pull` 후 `./scripts/install.ps1`과 가상 데이터 테스트를 다시 실행합니다. 기존 설정은 유지됩니다. 클라이언트 업데이트 후에는 실제 읽기 호환성도 재확인하세요.
