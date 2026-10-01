# stoom-openkkt

**dot이 PC 카카오톡 대화를 조회하고 업무 비서로 활용할 수 있게 하는 로컬 커넥터입니다.**

회사 대화방을 카카오톡의 사용자 범주로 모아 두고, dot에게 그 범주를 읽도록 지정하세요. 매번 대화를 내보내지 않아도 대화에서 업무 요청·일정·후속 조치를 찾아 정리하는 데 사용할 수 있습니다.

## 이렇게 사용합니다

> “업무와 프로젝트 범주를 봐줘.”
>
> “최근 대화에서 내가 처리해야 할 요청을 정리해줘.”
>
> “‘마감’이 언급된 메시지를 찾아줘.”

커넥터는 지정한 범주의 메시지를 제공하고, dot이 조회 결과를 바탕으로 검색·요약·업무 정리를 수행합니다. 카카오톡 원본은 수정하지 않습니다.

## 제공하는 기능

- **범주 선택** — 카카오톡에서 직접 만든 범주만 골라 연결
- **최근 대화 조회** — 요청할 때 현재 대화를 갱신해 읽기
- **본문 검색** — 선택한 방에서 키워드 찾기
- **변경 조회** — 이전 조회 이후의 관측 변경 확인
- **GUI 설정** — 데이터 폴더와 범주를 화면에서 선택하고 연결 상태 확인
- **로컬 MCP·CLI** — dot이 연결된 PC의 로컬 작업에서 사용

## 설치

Windows x64, 64비트 Python 3.11 이상, 설치되고 로그인된 PC 카카오톡이 필요합니다.

```powershell
git clone https://github.com/wansucong-stoom/stoom-openkkt.git
cd stoom-openkkt
./scripts/install.ps1
./.venv/Scripts/openkkt.exe gui
```

설정 화면에서 **데이터 폴더 찾기 → 설정 저장 → 범주 불러오기 → 범주 선택 저장** 순서로 진행합니다. 선택하기 전에는 대화 본문을 수집하지 않습니다.

현재는 소스에서 설치합니다. 자세한 요구사항과 수동 설치는 [설치 안내](docs/INSTALL.md)를 참고하세요.

## dot에 연결

dot의 **Computers**에서 이 PC에 접근을 허용한 뒤 로컬 MCP를 등록합니다. 이미 허용했다면 다시 설정할 필요가 없습니다.

```powershell
codex mcp add openkkt -- 'C:/path/to/stoom-openkkt/.venv/Scripts/python.exe' -m openkkt.cli --config 'C:/path/to/config.local.json' serve --live --allow-scope-changes
```

dot에게 연결된 PC의 로컬 작업으로 범주 목록을 확인하도록 요청한 뒤, 읽을 범주를 지정하세요. 실제 조회 결과를 받아 연결을 확인하는 절차는 [dot 연결 안내](docs/DOT-CONNECTION.md)에 정리했습니다.

## 개인정보

설정과 수집 DB는 개인 PC에 보관합니다. 공개 저장소에는 소스, 일반 설정 예시, 가상 테스트만 포함합니다. AI가 조회 결과를 사용하면 선택한 대화가 해당 AI 서비스로 전달될 수 있습니다. 자세한 보관 범위는 [개인정보 안내](docs/PRIVACY.md)를 참고하세요.

## 더 알아보기

- [설치와 설정](docs/INSTALL.md)
- [dot 연결과 MCP 도구](docs/DOT-CONNECTION.md)
- [문제 해결](docs/TROUBLESHOOTING.md)
- [개인정보 안내](docs/PRIVACY.md)

개발 검증은 `./.venv/Scripts/python.exe -m unittest discover -s tests -v`로 실행합니다. MIT 라이선스이며 Kakao 또는 OpenAI의 공식 제품은 아닙니다.
