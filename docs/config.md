# MatchRef — справочник по конфигурации

Все настройки лежат в `config/default_config.json`. Не редактируйте его напрямую —
переопределяйте ключи в `config/user_config.json` (он игнорируется git'ом и
**глубоко** мёржится поверх дефолтов: `matchref/config.py`). GUI пишет туда же.

Значения ниже — дефолтные. Сгруппировано по этапам пайплайна.

---

## Выбор клипов

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `clip_selection_mode` | `"auto"` | Что обрабатывать: `auto` (флагнутые, иначе все), `flagged`, `all_filtered`. |
| `selection_clip_color` | `"Purple"` | Цвет клипа, помечающий шот к обработке. |
| `selection_flag_color` | `"Purple"` | Цвет флага-маркера (альтернатива цвету клипа). |
| `auto_all_clips_fallback` | `true` | В режиме `auto`: если ничего не флагнуто — взять все клипы. |
| `skip_clip_name_patterns` | `["lock cut", "offline ref", "offline reference"]` | Подстроки в имени клипа, которые исключают его (сам референс на таймлайне). |
| `skip_reference_on_timeline` | `true` | Пропускать клип, совпадающий с offline-референсом. |
| `exclude_track_indices` | `[]` | Номера видеодорожек, которые не трогать. |
| `video_track_index` | `1` | Дорожка по умолчанию для выбора. |
| `max_clip_duration_frames` | `0` | Предупреждать, если клип длиннее (0 = выкл). Не блокирует. |

## Offline-референс и маппинг кадров

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `offline_reference_path` | `""` | Путь к lock-cut/offline видео. |
| `offline_mapping_mode` | `"auto"` | Как сопоставлять таймлайн→референс: `auto`, `conform` (строго по EDL/XML), `timeline`. |
| `conform_xml_path` / `conform_edl_path` | `""` | Conform-файл для точного маппинга кадра. Без него маппинг приблизительный. |
| `prefer_record_timecode_mapping` | `true` | Сопоставлять по record-TC, а не source-TC. |
| `normalize_conform_to_resolve` | `true` | Нормализовать индексы conform под нумерацию Resolve. |
| `retime_aware_source_mapping` | `true` | Учитывать ретайм клипа при вычислении source-кадра. |
| `reference_start_timecode` | `""` | Стартовый TC референса (если файл без TC-трека). |
| `lock_cut_hub_origin_frame` | `-1` | Origin-кадр lock-cut на хабе (−1 = авто). |
| `offline_timeline_offset_frames` | `0` | Ручной сдвиг индекса offline-кадра (legacy-путь). |
| `video_decode_index_offset` | `0` | Компенсация лага декодера на +1 кадр у некоторых кодеков. |
| `video_seek_mode` | `"refine"` | Стратегия перемотки: `refine` (точная), `msec`, `frames`. |
| `reference_clip_max_duration_frames` | `0` | Лимит длины референс-клипа (0 = выкл). |

## Сэмплирование кадров клипа

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `match_sample_mode` | `"three"` | Сколько точек сэмплить: `one`, `three`, и т.д. |
| `match_sample_point` | `"mid"` | Какая точка при одиночном сэмпле. |
| `sample_every_n_frames` | `0` | Плотное сэмплирование каждые N кадров (0 = выкл). |
| `dynamic_sampling_enabled` | `false` | Адаптивно добавлять сэмплы. |
| `alignment_frame_search_radius` | `0` | Искать лучший offline-кадр в ±радиусе (0 = выкл). |

## Выравнивание (ECC / feature matching)

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `alignment_method` | `"auto"` | `auto` (feature-инициализация + ECC), `ecc`, `features`. |
| `alignment_precision` | `"maximum"` | `maximum` включает пирамиду ECC + субпиксельный фазовый рефайн + Edit-рефайн. |
| `analysis_max_width` | `0` | Ширина анализа в px (0 = брать из таймлайна). |
| `input_scaling` | `"fit"` | Как вписывать source в канвас: `fit`, `fill`, `stretch`. |
| `ecc_motion_mode` | `"euclidean"` | Модель ECC: `translation`/`euclidean`/`affine`. `homography` автоматически заменяется на `affine` — Edit-страница перспективу не умеет, а пересчёт в пиксели таймлайна для неё некорректен. |
| `ecc_single_motion_mode` | `true` | Только основная модель. При `false` перебираются несколько; выбирается **наименьшая по DOF модель, прошедшая `ecc_threshold`** (а не просто с макс. скором — иначе аффин переобучается на шум). |
| `ecc_iterations` | `8000` | Макс. итераций ECC. |
| `ecc_epsilon` | `1e-08` | Критерий сходимости ECC. |
| `ecc_gauss_filt_size` | `5` | Сглаживание ECC. |
| `ecc_threshold` | `0.85` | Порог допуска ECC-скора к рефайну/успеху. |
| `ecc_use_overlay_mask` | `true` | Маскировать overlay-поля при ECC. |
| `ecc_pyramid_width_fractions` | `[0.25, 0.5, 1.0]` | Доли ширины для coarse-to-fine пирамиды. |
| `alignment_refine_translation` | `true` | Дорефайнить трансляцию отдельным ECC-проходом. |
| `refine_subpixel_translation` | `true` | Финальный субпиксель фазовой корреляцией. |
| `alignment_invert` | `false` | Инвертировать warp (online↔offline). |
| `match_use_clahe` | `true` | CLAHE-эквализация (гасит разницу грейда). |
| `match_edge_emphasis` | `0.35` | Доля градиента Собеля в подмешивании (инвариант к грейду). |
| `feature_max_count` | `8000` | Кол-во ORB-фич. |
| `feature_match_ratio` | `0.75` | Lowe ratio для отбора матчей. |
| `feature_match_threshold` | `0.85` | Порог допуска для feature-результата. |
| `feature_min_inlier_ratio` | `0.15` | Мин. доля inlier'ов RANSAC. |
| `lock_alignment_rotation` | `false` | Зануление поворота на этапе выравнивания. |
| `max_alignment_rotation_deg` | `3.0` | Лимит поворота — выше считается ошибкой. |
| `max_alignment_scale` / `min_alignment_scale` | `2.5` / `0.4` | Допустимый диапазон зума при применении. |
| `max_scale_spread_ratio` | `1.35` | Макс. разброс зума между сэмплами (иначе «несогласны»). |

## Маскировка overlay (выжженный TC/логотипы)

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `match_ignore_overlay` | `true` | Игнорировать поля с overlay при матчинге. |
| `match_crop_margin_ratio` | `0.08` | Базовое поле со всех сторон (доля). |
| `match_overlay_margin_bottom` | `0.12` | Увеличенное нижнее поле (бёрн-ин TC). |
| `match_overlay_margins` | `null` | Явные поля `[top,right,bottom,left]` (переопределяет выше). |

## Precision-рефайн (поиск Edit-параметров по NCC)

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `resolve_edit_refine` | `true` | Включить рефайн Edit-параметров рендером (только `maximum`). |
| `refine_max_width` | `1920` | Ширина рендера для рефайна. |
| `refine_multi_strategy` | `true` | Перебирать порядки осей (zoom/pos/rot). |
| `refine_strategy_orders` | `[[zoom,position,rotation], …]` | Порядки рефайна для перебора. |
| `refine_stage_passes` | `4` | Проходов уточнения на стадию. |
| `refine_fine_polish` | `true` | Финальная тонкая полировка на каждом разрешении. |
| `refine_zoom_steps` | `[0.03, 0.01, 0.002, 0.0005]` | Шаги поиска зума. |
| `refine_pan_steps` | `[12, 4, 1, 0.25]` | Шаги поиска pan/tilt (px). |
| `refine_rotation_steps` | `[0.4, 0.1, 0.025]` | Шаги поиска поворота (°). |
| `refine_cost_metric` | `"blend"` | Метрика стоимости: `blend`/`gradient`/`intensity`. |
| `refine_score_metric` | `"max"` | Как считать итоговый скор. |
| `refine_blend_gradient_weight` | `0.5` | Вес градиента в blend-метрике. |
| `refine_min_gradient_ncc` | `0.4` | Мин. градиентный NCC для приёмки рефайна. |
| `refine_fine_gradient_lock_threshold` | `0.6` | Порог «лока» по градиенту на тонкой стадии. |
| `refine_zoom_only_min_gradient` | `0.3` | Мин. градиент для доверия zoom-only пути. |
| `refine_early_exit_on_quality` | `true` | Останавливать рефайн при достижении качества. |
| `refine_stop_on_confident_sample` | `true` | Прекратить сэмплы при уверенном матче (см. аудит: ловушка с кейфреймами). |
| `confident_sample_score` | `0.96` | Порог «уверенного» сэмпла. |
| `min_ecc_for_refine_attempt` | `0.3` | Ниже этого ECC рефайн не запускается. |

### Pan/Tilt и Rotation в рефайне (реврейм)

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `refine_position_mode` | `"auto"` | Когда искать позицию: `auto`/`always`/`off`. |
| `refine_position_after_zoom` | `false` | Принудительно искать позицию (= `always`). |
| `refine_position_min_gain` | `0.12` | Минимальный выигрыш NCC, чтобы оставить Pan/Tilt (парсимония). |
| `refine_rotation_after_zoom` | `false` | Искать поворот после зума. |
| `auto_reframe_ncc_threshold` | `0.9` | NCC ниже → подозрение на реврейм, искать позицию. |
| `auto_reframe_translation_pixels` | `12.0` | Порог трансляции по оси для авто-реврейма. |
| `auto_reframe_translation_total_pixels` | `18.0` | Порог суммарной трансляции. |
| `snap_pan_tilt_below_pixels` | `2.0` | Pan/Tilt меньше этого → 0 (снап к центру). |
| `max_refine_pan_tilt_fraction` | `0.15` | Лимит Pan/Tilt как доля канваса. |

## Приёмка и контроль качества

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `accept_mode` | `"agreement"` | Логика приёмки клипа: `agreement`/`consensus`/… |
| `min_match_score` | `0.95` | Жёсткий порог скора перед применением (после рефайна). |
| `min_ok_samples_per_clip` | `1` | Сколько успешных сэмплов нужно. |
| `geometry_trust_min_score` | `0.7` | Порог доверия геометрии (rescue низкоструктурных шотов). |
| `geometry_agreement_zoom_tol` | `0.04` | Допуск согласия по зуму. |
| `ecc_consensus_rescue` | `true` | Спасать клип по согласию ECC-зумов, когда рефайн «гуляет». |
| `ecc_consensus_min_samples` | `2` | Мин. согласных сэмплов для consensus-rescue. |
| `transform_variance_threshold` | `0.02` | Порог стабильности трансформа между сэмплами. |
| `highlight_clip_dark_median` | `48.0` | Ниже этой медианы яркости — тёмный шот (клип хайлайтов). |
| `highlight_clip_median_mult` | `5.0` | Множитель клипа хайлайтов от медианы. |
| `match_highlight_clip` | `"auto"` | Клип ярких бликов: `auto`/`off`/число. |

## Запись в Edit Inspector (вывод)

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `edit_match_mode` | `"absolute"` | `absolute` (source-fit vs lock-cut → Inspector) или `delta` (композ на baseline). |
| `edit_priority_zoom` | `true` | Сначала зум, Pan/Tilt≈0 (если не реврейм). |
| `match_scale` | `true` | Применять зум. |
| `match_position` | `false` | Применять Pan/Tilt. |
| `match_rotation` | `false` | Применять поворот. |
| `compose_with_clip_transform` | `false` | Композить результат с текущим Edit-трансформом клипа. |
| `compensate_clip_transform` | `false` | Симулировать текущий Edit на source перед матчингом (delta-режим). |
| `apply_transform_select` | `"best"` | Какой сэмпл применять: `best`/`median`/последний. |
| `apply_median_transform` | `false` | Back-compat: медиана сэмплов. |
| `edit_zoom_base` | `1.0` | База зума (1.0 = 100%). >10 трактуется как процентный режим. |
| `edit_zoom_as_percent` | `false` | Зум как проценты. |
| `edit_pan_normalized` | `false` | Pan/Tilt в нормализованных, а не пикселях. |
| `edit_round_mode` | `"nearest"` | Округление значений: `nearest`/`up`. |
| `edit_zoom_decimal_places` | `4` | Знаков после запятой для зума. |
| `edit_pan_decimal_places` | `1` | Знаков для Pan/Tilt. |
| `edit_rotation_decimal_places` | `2` | Знаков для поворота. |
| `edit_keyframe_via_playhead` | `true` | Ставить значения, двигая плейхед (нужно для кейфреймов). |
| `verify_apply_readback` | `true` | После записи читать значения через `GetProperty` и сверять с целевыми; расхождение/частичная запись → клип помечается как ошибка, а не «Applied». |

### Знаки/инверсии осей

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `invert_pan_x` | `false` | Инвертировать Pan. |
| `invert_tilt_y` | `true` | Инвертировать Tilt (по умолчанию да — соглашение Resolve). |
| `invert_rotation` | `false` | Инвертировать поворот. |
| `transform_base_scale` | `1.0` | База зума в delta-режиме. |
| `transform_base_center` | `[0.5, 0.5]` | База центра. |
| `transform_base_angle` | `0.0` | База угла. |

## Кейфреймы (анимированный реврейм)

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `apply_keyframes_on_variance` | `true` | Ставить кейфреймы, если реврейм — плавный рамп. |
| `use_midpoint_keyframe` | `true` | Доп. ключ в середине при нестабильности. |
| `keyframe_zoom_spread` | `1.06` | Порог разброса зума, считающийся анимацией. |
| `keyframe_pan_delta_norm` | `0.01` | Порог дельты Pan для анимации. |

## FPS и разрешение

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `require_fps_match` | `true` | Требовать совпадения FPS source/таймлайн. |
| `fps_match_tolerance` | `0.01` | Допуск FPS. |
| `hub_fps_fallback` | `23.976` | FPS хаба, если не определён. |
| `require_resolution_match` | `false` | Требовать совпадения разрешения. |
| `warn_resolution_mismatch` | `true` | Предупреждать о расхождении разрешения. |

## Прочее / экспериментальное

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `media_cache_size` | `2` | Размер LRU-кэша декодеров source. |
| `lens_distortion_match_enabled` | `false` | Матчинг дисторсии (экспериментально). |
| `perspective_match_enabled` | `false` | Перспективный матчинг (экспериментально). |

## Отладка

| Ключ | Дефолт | Назначение |
|------|--------|-----------|
| `dry_run` | `false` | Анализ без записи в Inspector. |
| `debug_save_frames` | `false` | Сохранять кадры сравнения в `debug/`. |
| `debug_output_dir` | `""` | Папка для отладочных кадров (по умолчанию `debug/`). |
| `log_file` | `""` | Путь к лог-файлу (пусто = только консоль). |
