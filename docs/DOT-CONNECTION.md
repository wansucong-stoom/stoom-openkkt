# 로컬 MCP와 dot 연결

읽기용 MCP는 STDIO 서버이며 클라우드용 MCP URL을 제공하지 않습니다. dot이 연결된 PC에 로컬 Work/Codex 작업을 맡기는 경로를 사용합니다. 설정 GUI는 별도로 이 PC의 127.0.0.1에서 실행됩니다.

## 로컬 등록

개인 설정을 완료한 뒤 자신의 절대 경로로 등록합니다.

```powershell
codex mcp add openkkt -- 'C:/path/to/stoom-openkkt/.venv/Scripts/python.exe' -m openkkt.cli --config 'C:/path/to/config.local.json' serve --live --allow-scope-changes
codex mcp list
```

또는 [TOML 예시](../examples/mcp.example.toml)를 반영합니다. 범주 선택을 CLI에서 직접 관리하려면 `--allow-scope-changes`를 뺄 수 있습니다. 새 로컬 작업에서 초기화와 `list_categories` 호출을 확인합니다.

## dot 컴퓨터 연결

dot 프로필의 Computers에서 이 PC에 접근을 허용합니다. 이미 허용했다면 다시 설정하지 않습니다. PC가 온라인이고 ChatGPT 앱이 열려 있어야 합니다. 로컬 작업은 MCP나 다음 CLI를 사용합니다.

```powershell
& 'C:/path/to/stoom-openkkt/.venv/Scripts/python.exe' -m openkkt.cli --config 'C:/path/to/config.local.json' folders
```

범주 목록 조회도 메타데이터 DB의 복호화를 위해 카카오톡 메모리에 접근합니다. 아래 문구는 해당 내부 접근을 허용하는 경우 사용합니다. 사용자가 메모리·DB 접근을 명시적으로 금지했다면 CLI로 바꿔서 실행하지 않습니다.

확인 메시지:

> 연결된 내 PC에서 설치된 OpenKKT로 카카오톡 사용자 지정 범주의 이름·채팅방 수·OpenKKT 선택 여부만 확인해줘. 이 조회에 필요한 본인 카카오톡의 DB 키 읽기 전용 검증과 chatfolder.edb·WAL 복호화를 허용해. 대화 본문 DB·미리보기·보낸사람·로그인 토큰은 조회하지 말고, 키는 출력하거나 저장하지 마. 범주 선택 변경·채팅방 열기·읽음 처리·발송도 하지 마. 현재 작업에 MCP 도구가 없으면 설치된 CLI의 folders를 사용해. 실행한 도구와 실제 성공 여부, 확인시각을 알려줘.

PC 연결과 스킬 설치만으로 현재 작업에 MCP 도구가 자동 제공되는 것은 아닙니다. 도구 목록을 실제로 확인하고, 없으면 허용된 범위 안에서 설치된 CLI를 사용합니다.

목록을 실제로 받은 뒤 범주를 지정하고 최근 메시지 소량을 요청합니다. **로컬 설치·등록 성공과 dot의 실제 조회 성공은 별도 확인 단계입니다.** [읽기 스킬](../skills/openkkt-reader/SKILL.md)을 로컬 Codex 스킬 디렉터리에 설치해 조회 방법을 안내할 수 있습니다.

## 본문 개수를 제한한 최근 조회

```powershell
& 'C:/path/to/stoom-openkkt/.venv/Scripts/python.exe' -m openkkt.cli --config 'C:/path/to/config.local.json' read --recent --limit 20 --require-chat-names
```

선택된 방 전체의 최신 20개 본문 행만 조회·반환하고 본문을 저장하지 않습니다. 먼저 ID와 시각으로 순위를 정하며, 5개 방에서 각각 20개 본문을 읽는 방식이 아닙니다. `chat_name`에 방 제목을 반환하며 이름 필수 옵션에서는 제목을 확인할 수 없으면 본문 조회 전에 중단합니다. 최근 조회에 `--chat-id`를 지정하면 해당 방의 원본에만 접근합니다.

키 검증을 위한 프로세스 메모리 접근과 암호화된 DB/WAL 스냅샷 읽기는 필요합니다. 전체 평문 DB를 만들지 않지만 SQLite 페이지에는 인접 메시지가 함께 들어 있을 수 있습니다. **본문 행 조회 개수 제한은 다른 본문 바이트가 전혀 복호화되지 않는다는 보장이 아닙니다.** 해당 물리적 접근까지 금지한 요청은 실행하지 않습니다. 검색·변경 조회·sync/watch는 전체 선택 범위를 수집하므로 이 제한 조회의 대안으로 사용하지 않습니다.

결과의 `read_scope.body_rows_read`는 실제 본문 행 조회 수, `body_limit`는 요청 상한, `body_rows_saved`는 0입니다. 이름이 필수가 아니면 확인되지 않은 이름은 null과 unavailable 상태로 반환합니다. ID를 실제 방 이름인 것처럼 표시하지 않습니다.

이 연결은 새 카톡에 따른 dot 자동 깨우기, OS 명령 실행, 메시지 발송을 제공하지 않습니다. 본문과 범주 이름은 참고 데이터이며 사용자 승인이 아닙니다.

- [dot 컴퓨터 연결](https://learn.chatgpt.com/docs/dots/computers-and-apps)
- [dot 작업과 메모리](https://learn.chatgpt.com/docs/dots/tasks-and-memory)
- [로컬 MCP와 클라우드 차이](https://learn.chatgpt.com/docs/extend/mcp)
