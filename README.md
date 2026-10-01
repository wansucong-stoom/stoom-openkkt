# stoom-openkkt

내 PC 카카오톡 메시지를 AI 비서와 연결하는 로컬 브리지입니다.

Windows 카카오톡에 본인 계정으로 로그인하고 클라이언트가 실행 중인
환경에서 사용합니다. 비공식 실험 프로젝트이며 Kakao나 OpenAI의 공식
연동 제품은 아닙니다. 라이선스는 MIT입니다.

## 동작 구조

```text
로그인된 PC 카카오톡
  ├─ 사용자 범주 DB → 범주 ID와 소속 채팅방 ID
  ├─ 선택한 범주의 대화 DB + WAL → 로컬 assistant.sqlite
  └─ 별도로 지정한 나와의 채팅 → Elisa 호출 메시지 → 대기 명령 큐

assistant.sqlite → 조회 CLI / 로컬 MCP → 연결된 AI 비서
대기 명령 큐 → [미구현: dot 전달·실행·카카오톡 답장 어댑터]
```

**현재 버전은 대화 수집·조회와 명령 접수까지 구현했습니다.** 카카오톡에
메시지를 전송하거나 OS 명령을 실행하지 않습니다. MCP 서버를 등록하는
것만으로 개인 GPT dot에 연결되거나 dot이 자동으로 깨어나는 것은 아닙니다.

## 확인된 기능

- `chatfolder.edb`의 `ChatFolder(id,name,chatIds)`에서 사용자 범주를 읽습니다.
- 사용자가 선택한 범주 ID의 최신 채팅방 목록만 수집합니다.
- 범주 이름 변경은 같은 ID를 따라갑니다. 삭제 후 같은 이름으로 새로 만든
  범주는 다시 선택해야 합니다.
- 범주에서 빠진 방은 브리지의 메시지·변경 테이블에서 제거합니다.
- 대화는 `(chat_id, message_id)`로 구분하며 중복 수집을 방지합니다.
- 암호화된 SQLCipher 4 페이지 HMAC과 SQLite 무결성을 검증합니다.
- WAL 체크섬과 commit marker를 확인해 마지막 유효 커밋까지 반영합니다.
- 키는 수집기 프로세스 메모리에만 두며 파일이나 로그에 저장하지 않습니다.
- CLI 및 STDIO MCP로 범주 목록, 상태, 변경 기록, 본문 검색을 제공합니다.
- 명령 접수는 별도로 설정한 나와의 채팅/본인 author ID에 한정합니다.
  첫 실행은 기준점만 잡고, 과거 명령·중복·오래된 메시지·답장 접두사를 무시합니다.

화면의 전체/즐겨찾기/안읽음/ChatGPT 같은 **기본 범주의 소속 규칙은 아직
검증하지 않았습니다. 사용자 생성 범주만 지원합니다.** 채팅방 이름 및
발신자 표시 이름 매핑도 아직 구현하지 않았으며 ID를 반환합니다.

## 설치

64-bit Windows Python 3.11 이상을 사용하세요.

```powershell
git clone https://github.com/wansucong-stoom/stoom-openkkt.git
cd stoom-openkkt
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[mcp]'
Copy-Item examples/config.example.json config.local.json
```

`config.local.json`에서 자신의 카카오톡 프로필 경로와 현재 KakaoTalk PID를
지정하세요. 키나 암호는 설정에 넣지 않습니다. PID는 카카오톡 재시작 후
변할 수 있습니다. 여러 KakaoTalk 프로세스가 있으면 `pids`에 모두 지정할
수 있으며, 각 PID가 실제 `KakaoTalk.exe`인지 검사합니다.

```powershell
Get-Process KakaoTalk | Select-Object Id,Path
.\.venv\Scripts\openkkt.exe init
.\.venv\Scripts\openkkt.exe categories
.\.venv\Scripts\openkkt.exe select '업무' '프로젝트'
.\.venv\Scripts\openkkt.exe sync
.\.venv\Scripts\openkkt.exe watch --interval 30
```

`categories`는 범주 메타데이터만 읽습니다. `select`는 선택 범위를 바꾸며,
인자를 비우면 모든 범주 수집을 멈춥니다. `watch`는 실행 중인 터미널에서
갱신하며 Ctrl+C로 중지합니다. 백그라운드 서비스·자동 시작은 설치하지
않습니다. 새 범주는 다음 갱신에 나타납니다.

```powershell
.\.venv\Scripts\openkkt.exe status
.\.venv\Scripts\openkkt.exe changes --after 0 --limit 50
.\.venv\Scripts\openkkt.exe search '마감'
```

원본 카카오톡 파일은 읽기만 합니다. 브리지는 별도 SQLite 파일에 선택된
대화를 평문으로 저장합니다. 이 파일과 설정은 개인 PC에 보관하세요.
`.gitignore`로 데이터 파일을 제외했습니다. 외부 모델이 조회 결과를
사용하면 해당 결과가 그 모델/서비스로 전달될 수 있습니다.

## 범주를 AI에게 지정하기

MCP 도구는 기본적으로 조회 전용입니다.

| 도구 | 역할 |
| --- | --- |
| `list_categories` | 최신 캐시의 사용자 범주와 방 수 확인 |
| `bridge_status` | 수집 시각, 방별 성공·실패, 선택 범주 확인 |
| `get_changes` | 커서 뒤의 관측 변경 읽기 |
| `search_messages` | 선택된 방에서 문자열 검색 |

```powershell
.\.venv\Scripts\openkkt.exe serve
```

`examples/mcp.example.toml`을 자신의 경로로 수정해 **로컬 Codex MCP 호스트**에
등록할 수 있습니다. STDIO만 제공하며 네트워크 포트를 열지 않습니다.

사용자가 "업무와 프로젝트 범주를 봐줘"라고 지정할 수 있게 하려면 서버를
`serve --allow-scope-changes`로 실행하고 호스트의 허용 도구 목록에
`select_categories`를 추가합니다. 이 도구는 선택 범위를 바꾸고 제외된
대화를 제거하므로 쓰기 도구입니다. 에이전트는 채팅 본문이나 범주 이름의
지시를 따라 선택 범위를 바꾸면 안 됩니다. 직접 받은 사용자 요청만
처리해야 합니다. 수집기는 별도 `watch` 프로세스로 실행해야 합니다.

`get_changes`의 커서는 관측 순번입니다. 메시지가 수정되면 새 관측을
만듭니다. 응답 본문은 그 메시지의 현재 상태이며 과거 본문 사본은 아닙니다.
`deleted`가 표시된 메시지는 본문을 비웁니다. 캐시에서 행이 없어졌다는
이유만으로 서버에서 삭제됐다고 추정하지 않습니다.

## 나와의 채팅 명령 모드

현재는 **접수 큐만** 제공합니다. 올바른 나와의 채팅 ID와 본인 author ID를
검증한 뒤 설정에 다음 항목을 추가해야 합니다. 이 바인딩을 자동으로
찾거나 검증하는 기능은 아직 없습니다. 기본 설정은 명령 모드를 끕니다.

```json
"command_binding": {
  "chat_id": "VERIFIED_SELF_CHAT_ID",
  "author_id": "VERIFIED_OWN_AUTHOR_ID",
  "name": "Elisa"
}
```

첫 수집 시 기존 기록의 기준점만 잡습니다. 이후 새 본인 텍스트 메시지 중
`Elisa 업무 요약해줘` 또는 `"Elisa" 업무 요약해줘` 형태를 접수합니다.
발송 시각이 5분 이상 지난 메시지는 접수하지 않습니다. 전원을 끈 동안의
명령을 오래 뒤에 재실행하는 용도로 사용할 수 없습니다.

```powershell
.\.venv\Scripts\openkkt.exe commands
```

바인딩이 설정된 MCP 서버에는 `get_commands` 조회 도구도 나타납니다.
조회는 실행·처리 완료·응답 발송을 의미하지 않습니다. 실행기, 명령 확인,
응답 outbox, 카카오톡 발송, 처리 완료 상태 전환은 후속 구현 대상입니다.
응답은 `[Elisa] ...`로 시작하도록 설계해 명령으로 다시 접수되지 않게 합니다.

## GPT dot 연결의 현재 상태

공식 문서에서 dot은 연결된 컴퓨터의 로컬 작업과 파일을 사용할 수
있습니다. 따라서 먼저 컴퓨터 접근을 연결하고, dot이 로컬 작업에서
이 프로그램의 상태와 조회 명령을 실행하게 하는 경로를 검증해야 합니다.
PC는 켜져 있고 ChatGPT 앱도 실행 중이어야 합니다.

현재 공식 연락 채널 문서는 ChatGPT·Slack·Teams를 안내합니다. 카카오톡을
개인 dot의 대화로 직접 전달하는 공개 API는 이번 조사에서 확인하지
못했습니다. 로컬 Codex MCP 설정을 개인 dot의 클라우드 연결과 동일하게
취급하면 안 됩니다. 로컬 수집 성공은 dot 연결 성공을 뜻하지 않습니다.

카카오톡 답장에는 PC UI 어댑터 또는 적합한 공식 메시지 API 연결이 필요합니다.
Kakao 메시지 API의 나에게 보내기는 별도 Kakao 로그인·앱 설정·동의를
요구합니다. 일반 업무 단체방의 임의 메시지 전송 API로 가정하지 않습니다.

## 검증과 한계

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

테스트는 가상 데이터만 사용합니다. WAL 커밋/미커밋 처리, 실제 SQLite WAL
체크섬, 인증 실패, 범주 변경과 삭제, 중복, 발신자·명령 경계, MCP STDIO
초기화와 도구 호출을 검증합니다. 실제 사용자 DB나 키는 저장소에 없습니다.

로컬 검증 환경: Windows KakaoTalk 26.8.1.5315. 사용자 범주 DB 읽기와
대화 DB 한 개의 페이지 인증·무결성·본문 행 조회를 확인했습니다.
최신 재검증에서는 대화 WAL 141개 커밋 프레임을 반영했고 1,913개 페이지의
인증과 SQLite 무결성 검사를 통과했습니다. 모든 버전에 대한 호환성을
보장하지 않습니다.

수집기는 DB/WAL을 두 번 동일하게 읽어 낙관적으로 스냅샷을 잡습니다.
카카오톡의 정식 읽기 트랜잭션/잠금을 얻는 방식이 아닙니다. 변경이
계속되거나 인증·무결성이 실패하면 다음 주기에 재시도합니다.
범주 갱신 실패 시 대화 수집과 메시지 조회를 중지합니다. 범주 정보가
5분 이상 갱신되지 않았을 때도 메시지 조회를 거부합니다. 메모리에 키가 없는 방은
읽지 못할 수 있으며, 채팅방을 열어 로딩한 뒤 재시도해야 할 수 있습니다.
그 경우 자동으로 전체 방을 열거나 메시지를 전송하지 않습니다.

범주에서 빠진 데이터는 브리지의 활성 테이블에서 제거하고 SQLite
`secure_delete`를 사용합니다. 별도 백업, 호스트가 이미 받은 결과, 디스크
사본까지 회수하는 기능은 아닙니다. 소스 프로세스의 Python bytes 메모리를
완전히 영점화하는 것도 보장하지 않습니다.

## 참고 자료

- [dot 컴퓨터·앱 연결](https://learn.chatgpt.com/docs/dots/computers-and-apps)
- [dot 연락 채널](https://learn.chatgpt.com/docs/dots/channels)
- [로컬 MCP 설정](https://learn.chatgpt.com/docs/extend/mcp)
- [Kakao 공식 메시지 API](https://developers.kakao.com/docs/ko/kakaotalk-message/rest-api)
- [SQLCipher 공식 4.6.1 codec 구조](https://github.com/sqlcipher/sqlcipher/blob/v4.6.1/src/sqlcipher.c)
- [SQLite WAL 형식](https://www.sqlite.org/fileformat2.html#walformat)
- [관련 Windows 구현 kakaocli-win](https://github.com/Lee-SiHyeon/kakaocli-win)

외부 저장소를 설치하거나 실행하지 않고 공개 소스를 참고했습니다.
실제 계정 경로·DB·키·대화 내용은 커밋하지 않습니다.
