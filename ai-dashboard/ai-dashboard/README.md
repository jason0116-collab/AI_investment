# AI Productivity Investment Dashboard — 완전 자동 수집판

**수동 입력 데이터가 전혀 없습니다.** 모든 지표를 공개 API에서 직접 수집합니다.
GitHub에 올려 GitHub Actions + GitHub Pages로 매일 자동 갱신되는 구조입니다.

```
매일 GitHub Actions
  └─ collect.py 실행 → data.json 생성/커밋
       ├─ FRED (미국 금리·물가·침체확률·VIX·생산성·부채)
       ├─ ECOS 한국은행 (기준금리·국고채 10년·CPI)
       ├─ SEC EDGAR (Hyperscaler CAPEX, Micron 매출)
       ├─ pykrx (KOSPI EPS·배당수익률·기술적 추세, RIM 종목 펀더멘털)
       └─ yfinance (XLK·SOXX·S&P500 가격, QQQ 내재 이익성장률)
  └─ GitHub Pages 배포
index.html
  └─ data.json만 읽어서 렌더링 (서버·프록시·수동입력 없음)
```

---

## 1. GitHub 설정 (최초 1회)

### 1) 저장소에 이 파일들을 올립니다
```
collect.py
requirements.txt
index.html
.github/workflows/update-data.yml
```

### 2) 인증키를 Secrets에 등록
저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| Secret 이름 | 필수 여부 | 발급처 |
|---|---|---|
| `ECOS_API_KEY` | **필수** (한국 지표 3개) | https://ecos.bok.or.kr/api/ → 회원가입 → 인증키 신청 (활성화까지 보통 1일) |
| `FRED_API_KEY` | 선택 (없어도 CSV로 자동 수집됨) | https://fredaccount.stlouisfed.org/apikeys |

### 3) GitHub Pages 활성화
저장소 → **Settings → Pages → Build and deployment → Source: GitHub Actions**

### 4) 첫 실행
**Actions 탭 → Update Dashboard Data → Run workflow** 로 수동 실행하거나, `collect.py`를 push하면 자동 실행됩니다.
이후에는 매일 00:10 UTC(09:10 KST)에 자동 실행됩니다 (`.github/workflows/update-data.yml`의 cron으로 변경 가능).

완료되면 `https://<계정>.github.io/<저장소명>/` 에서 대시보드를 볼 수 있습니다.

---

## 2. 로컬에서 먼저 확인하기 (선택)

```bash
pip install -r requirements.txt
export ECOS_API_KEY="발급받은키"     # 선택: export FRED_API_KEY="..."
python collect.py                    # data.json 생성
python -m http.server 8000           # index.html을 로컬에서 확인
# 브라우저에서 http://localhost:8000 접속
```

---

## 3. 지표 목록 (전량 자동)

| 구분 | 지표 | 소스 | 비고 |
|---|---|---|---|
| Macro | 미국 노동생산성 YoY | FRED `OPHNFB` | |
| Macro | Core PCE YoY | FRED `PCEPILFE` | |
| Macro | 미국 10Y 금리 | FRED `DGS10` | |
| Macro | 경기침체 확률 | FRED `RECPROUSM156N` | |
| Macro | VIX | FRED `VIXCLS` | |
| Debt | 미국 가계부채/GDP | FRED `HDTGPDUSQ163N` | |
| Debt | 한국 가계부채/GDP | FRED `HDTGPDKRQ163N` | IMF 제공, 한국은행 자체 통계와 소폭 차이 가능 |
| Debt | 미국 비금융기업 신용/GDP | FRED `QUSNAM770A` | BIS 정의 |
| Korea | 한국 기준금리 | ECOS `722Y001` | 참고용(가중치 0) |
| Korea | 한국 국고채 10년 | ECOS `817Y002` | RIM 무위험금리로도 사용 |
| Korea | 한국 CPI YoY | ECOS `901Y009` | |
| Earnings | Nasdaq(QQQ) 내재 이익성장률 | yfinance | `trailingPE/forwardPE−1` 근사치 |
| Earnings | KOSPI EPS 증가율 | pykrx | 지수 `종가/PER`의 YoY |
| AI Cycle | Hyperscaler CAPEX 증가율 | SEC EDGAR | MSFT·GOOGL·AMZN·META 연간(10-K) 합산 근사 |
| AI Cycle | Micron 매출 YoY (HBM 수요 근사) | SEC EDGAR | 연간(10-K) 기준 |
| Valuation | 현재가 ÷ RIM 적정가 | RIM 모델 | BPS·ROE·현재가 = pykrx 자동, 무위험금리 = ECOS 자동 |
| 환원 | KOSPI 배당수익률 | pykrx | |
| 기술적 | KOSPI 기술적 추세 | pykrx | 200일선 + 52주 고저 기준 |

### 알아둘 근사치
- **Hyperscaler CAPEX**: 4개사 회계연도 종료월이 달라(MSFT 6월 vs 나머지 12월) 완전한 동시점 비교는 아닙니다.
- **HBM 수요**: 개별 HBM 출하량 통계가 무료로 없어 Micron 연간 매출 성장률로 근사했습니다.
- **Nasdaq EPS**: 실제 컨센서스 EPS 대신 PER 역수 관계로 추정한 내재 성장률입니다.
- 연간(10-K) 데이터 특성상 CAPEX·HBM 지표는 회사가 연차보고서를 낼 때만 갱신됩니다(분기 데이터는 XBRL 누적치 이슈로 사용하지 않음).

## 4. RIM 모델

- 대상 종목: `collect.py`의 `RIM_TICKER`(기본값 `005930` 삼성전자) — 다른 종목으로 바꾸려면 이 값만 수정
- BPS·PER·PBR·현재가: pykrx 자동, ROE = PBR÷PER
- 무위험금리: ECOS 국고채 10년 자동
- 영구성장률(g)·리스크프리미엄 그리드는 표준 가정값(모델 파라미터이지 수집 데이터가 아님) — `collect.py`의 `RIM_ASSUMPTIONS`에서 조정 가능

## 5. 결측 처리 정책

특정 지표 수집이 실패하면:
- **수동 기본값으로 대체하지 않습니다** — 해당 지표는 `null`로 남고 점수 계산의 분자·분모에서 완전히 제외됩니다(가중치 재분배)
- 대시보드 상단 빨간 박스와 지표 테이블의 "N/A" 행에 실패 사유가 그대로 표시됩니다
- 다음 자동 실행(또는 Actions 탭에서 수동 실행)에서 재수집됩니다

## 6. 커스터마이징

- 점수 밴드·가중치: `index.html`의 `IND` 배열, `LEVEL_PTS`
- 수집 대상 회사/시리즈: `collect.py`의 `FRED_SERIES` / `ECOS_SERIES` / `HYPERSCALERS` / `RIM_TICKER`
- 갱신 주기: `.github/workflows/update-data.yml`의 `cron`
