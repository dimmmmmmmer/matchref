# MatchRef — Offline Reference Transform Match

Python-инструмент для **DaVinci Resolve Studio 20+**, который восстанавливает на таймлайне трансформы клипов (Zoom, Pan, Tilt, Rotation) по оффлайн-референсу через **Edit Inspector**, используя OpenCV ECC / feature matching.

## Требования

- DaVinci Resolve **Studio** 20 или новее (Scripting API)
- Python 3 с пакетами из `requirements.txt`
- PySide6 для GUI
- OpenCV + NumPy

## Установка

На macOS с Homebrew Python **нельзя** ставить пакеты в систему (`externally-managed-environment`, PEP 668). Используйте виртуальное окружение:

```bash
cd /path/to/matchref
chmod +x setup.sh
./setup.sh
source .venv/bin/activate
python main.py
```

Вручную:

```bash
cd /path/to/matchref
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

> **Resolve:** скрипты внутри Resolve используют **встроенный Python** Blackmagic, не этот venv. Venv нужен для GUI/отладки снаружи. Для запуска из меню Resolve достаточно скопировать проект; OpenCV/PySide6 должны быть доступны в среде Resolve (часто уже есть) или ставятся по документации BMD.

### Запуск из Resolve

**Не нажимайте** `Scripts → matchref → main` — Resolve запускает встроенный Python без PySide6/OpenCV, окно не появится.

Правильная установка (macOS):

```bash
cd /Users/enrvate/Documents/matchref
./setup.sh          # если ещё не делали
chmod +x install_resolve.sh
./install_resolve.sh
```

В Resolve: **Workspace → Scripts → Utility → MatchRef** (один пункт, без вложенных папок).

Структура после установки:

```
.../Fusion/Scripts/Utility/
  MatchRef.py          ← launcher
  matchref/
    main.py
    matchref/          ← пакет
    .venv/
    config/
```

Ручной запуск GUI при открытом Resolve: `source .venv/bin/activate && python main.py`

### Запуск снаружи (отладка GUI)

```bash
source .venv/bin/activate
python main.py
```

Headless:

```bash
source .venv/bin/activate
python main.py --no-gui --analyze   # анализ (запись только если dry_run=false в config)
python main.py --no-gui --apply     # анализ + запись в Inspector (dry_run принудительно off)
```

> `--apply` всегда заново анализирует текущий таймлайн: отчёт анализа держит живые ссылки
> на объекты Resolve API и не сохраняется между запусками процесса.

## Сопоставление кадров offline

| Источник | Когда используется |
|----------|-------------------|
| **Hub** | **Таймлайн DaVinci Resolve** — единая шкала: `GetStart()` клипа + local frame. |
| **EDL / XML** | Record TC из conform автоматически сдвигается в hub (origin из `<timecode>` XML или первого cut). |
| **Lock cut** | Тот же hub frame → кадр в mp4 (если референс с 00:00:00:00, hub 120 = offline 120). |
| **Fallback** | Без conform: hub frame напрямую в offline (+ `offline_timeline_offset_frames`). |

Поддерживается CMX 3600 EDL (`* FROM CLIP NAME`, `* SOURCE FILE`) и базовый FCPXML / Premiere XMEML.

Параметры в config:

- **FPS и разрешение** — только с открытого таймлайна Resolve (в config не задаются)
- `require_fps_match` — стоп, если XML/EDL или lock-cut не совпадают с timeline FPS
- `normalize_conform_to_resolve` — привести EDL/XML record TC к шкале Resolve (по умолчанию `true`, origin из XML автоматически)
- **4K ref / 1080 timeline** — номера кадров = Resolve hub; картинки масштабируются для ECC (разное разрешение — warning, не ошибка)
- `reference_start_timecode` — только если lock-cut **файл** начинается не с hub 0 (редкий случай)
- `offline_mapping_mode`: `auto` | `conform` | `timeline`

## Рабочий процесс

1. Откройте **online** таймлайн после конформа.
2. Подготовьте **offline reference** — рендер оффлайн-монтажа (record TC из EDL = позиция в файле).
3. *(Опционально)* Укажите **XML** разбивки таймлайна (для таймкодов record → lock cut).
4. Пометьте все **online-шоты**: правый клик → **Clip Color → Purple** (в Resolve нет массовых Flags). Lock cut только в Offline Reference, не красьте его на таймлайне.
5. Запустите MatchRef, укажите offline-файл.
6. **Run MatchRef** — анализ (start / mid / end) и, если Dry Run выключен, сразу запись в **Edit Inspector**.

## Выбор клипов

API Resolve не всегда отдаёт выделение таймлайна. Режимы в `config/user_config.json`:

| `clip_selection_mode` | Поведение |
|----------------------|-----------|
| `auto` | Selected API → **Clip Color** → flags → all_filtered |
| `clip_color` | Клипы с цветом `selection_clip_color` (Purple, Orange, …) |
| `selected` | Только `GetSelectedTimelineItems()` (если есть) |
| `flagged` | Клипы с флагом (редко в UI) |
| `all_filtered` | Все video-клипы минус lock cut |
| `playhead` | Клип под плейхедом |
| `track` | Все клипы на `video_track_index` |

## Конфигурация

- Шаблон: `config/default_config.json`
- Пользовательский: `config/user_config.json` (создаётся из GUI)

Ключевые параметры:

- `ecc_threshold` — порог корреляции ECC (ниже — клип помечается как проблемный)
- `transform_variance_threshold` — порог различия start/mid/end для решения о среднем ключе
- `offline_timeline_offset_frames` — сдвиг таймкода offline-файла относительно таймлайна
- `dry_run` — анализ без записи в Inspector

### Подгон трансформа (refine)

Online — сырой материал, lock-cut — уже с грейдом, поэтому совпадение считается так, чтобы цветокоррекция не мешала геометрии:

- `refine_cost_metric` (`gradient` | `intensity` | `blend`) — метрика, по которой ищется трансформ. `gradient` (по умолчанию) сравнивает карты краёв (Sobel) — инвариантно к грейду и резко локализует (точный подгон, без ухода в мусорный сдвиг).
- `refine_score_metric` (`max` | `intensity` | `gradient`) — метрика приёмки. `max` (по умолчанию): некрашеный клип проходит по яркости, крашеный — по структуре краёв.
- `refine_stage_passes` — сколько раз повторять стадии zoom/position/rotation до сходимости (по умолчанию 4). Zoom и position связаны, один проход оставляет zoom неоптимальным — это и был остаточный «почти, но не до конца».
- `auto_reframe_ncc_threshold` / `min_match_score` — порог приёмки (0.95). Если сильно крашеные клипы всё ещё отбраковываются **до** refine (в логе `ECC … < admit`), снизьте `min_ecc_for_refine_attempt` (по умолчанию 0.70).

## Архитектура

```
matchref/
  config.py            — загрузка/сохранение JSON
  models.py            — датаклассы результатов (sample / clip / report)
  pipeline.py          — оркестрация analyze→apply с колбэками статуса
  gui.py               — PySide6 GUI (анализ в фоновом QThread)

  resolve_api.py       — подключение к Resolve, чтение fps/разрешения, items
  timeline_context.py  — таймлайн Resolve как hub (fps, raster)
  selection.py         — выбор клипов (selected / clip color / flags / all)
  clip_filter.py       — фильтрация целевых клипов и lock-cut
  clip_metadata.py     — reel / source frame из Resolve

  timecode.py          — SMPTE ↔ frames (drop / non-drop)
  timebase.py          — единый hub-таймбейс, origin conform→hub
  fps.py / fps_check.py — нормализация и проверка fps против таймлайна
  conform_edl.py       — CMX 3600 EDL
  conform_xml.py       — FCPXML / XMEML
  conform_index.py     — поиск кадра offline по reel + source TC
  lock_cut_align.py    — авто-origin lock-cut в hub

  media_probe.py       — fps/метаданные файла через OpenCV
  frame_read.py        — чтение кадра по индексу (msec / frames / refine)
  frame_provider.py    — кадры online/offline + кэш VideoCapture

  alignment.py         — ECC / feature matching, разложение affine
  precision_align.py   — пирамидальный ECC, phase-correlation, refine в Resolve Edit
  refine_strategies.py — порядок refine (zoom / position / rotation)
  overlay_crop.py      — кроп оверлеев / ECC-маска
  reframe_detect.py    — авто-детект reframe из warp
  match_quality.py     — пороги/гейты качества матча
  transform_analysis.py — основной анализ клипа (sample → alignment → refine)
  transform_convert.py — warp → параметры Edit Inspector
  clip_edit_transform.py — чтение/симуляция текущего Edit-трансформа клипа
  edit_match_mode.py / edit_quantize.py — режим (absolute/delta) и квантизация
  edit_apply.py        — запись Pan/Tilt/Zoom/Rotation в Edit Inspector

  debug_frames.py      — сравнительные кадры в папку debug
  logging_report.py    — логирование и итоговый отчёт
  extensions.py        — заглушки Perspective / Lens / Dynamic Sampling
```

## Логирование

В консоль и опционально в файл (`log_file`) выводятся:

`Clip Name | Frame | Scale | Position X/Y | Rotation | ECC Score`

По завершении анализа формируется итоговый отчёт.

## Ограничения

- Без EDL/XML сопоставление предполагает, что offline-референс **совпадает по record-позиции** с online-таймлайном.
- С EDL/XML нужен reel/source TC, совпадающие с событиями в conform-файле.
- Online-кадры читаются из исходного файла Media Pool; offline — из указанного reference-файла.
- Сильные отличия цветокоррекции/кропа/пересвета снижают ECC score.
- Трансформы только через Edit Inspector (не Fusion).

## Будущие режимы

В config предусмотрены флаги (пока не реализованы):

- `perspective_match_enabled`
- `lens_distortion_match_enabled`
- `dynamic_sampling_enabled` / `sample_every_n_frames`

## Разработка

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q     # модульные тесты (без Resolve)
ruff check matchref tests       # линтер
ruff format matchref tests      # форматирование
```

Тесты покрывают чистую логику (timecode, маппинг conform, гейты качества) и не требуют
запущенного DaVinci Resolve. Конфиг линтера — в `pyproject.toml`.

## Лицензия

MIT (при необходимости уточните у автора проекта).
