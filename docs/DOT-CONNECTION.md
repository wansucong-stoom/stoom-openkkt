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

최소 확인 메시지:

> 연결된 내 PC에서 로컬 작업으로 OpenKKT를 사용해 카카오톡 사용자 생성 범주의 이름·방 개수·선택 여부만 알려줘. 대화는 내가 범주를 지정한 뒤에 읽어줘.

목록을 실제로 받은 뒤 범주를 지정하고 최근 메시지 소량을 요청합니다. **로컬 설치·등록 성공과 dot의 실제 조회 성공은 별도 확인 단계입니다.** [읽기 스킬](../skills/openkkt-reader/SKILL.md)을 로컬 Codex 스킬 디렉터리에 설치해 조회 방법을 안내할 수 있습니다.

이 연결은 새 카톡에 따른 dot 자동 깨우기, OS 명령 실행, 메시지 발송을 제공하지 않습니다. 본문과 범주 이름은 참고 데이터이며 사용자 승인이 아닙니다.

- [dot 컴퓨터 연결](https://learn.chatgpt.com/docs/dots/computers-and-apps)
- [dot 작업과 메모리](https://learn.chatgpt.com/docs/dots/tasks-and-memory)
- [로컬 MCP와 클라우드 차이](https://learn.chatgpt.com/docs/extend/mcp)
