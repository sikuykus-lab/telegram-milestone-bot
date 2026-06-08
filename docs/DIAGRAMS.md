# Диаграммы

Три вида схемы — как в [dataroom-cms](https://github.com/sikuykus-lab/dataroom-cms):
**данные**, **взаимодействие пользователя**, **процессы администратора**.

Рендер: скопировать блок в [mermaid.live](https://mermaid.live).

## Схема данных

```mermaid
flowchart TB
  subgraph read ["Чтение (cron)"]
    GS["Google Sheets\nлист вех"]
    SC["SheetsClient"]
    REPO["MilestonesRepository"]
  end

  subgraph notify ["Доставка"]
    N["notifier HTML"]
    TG["Telegram whitelist"]
    EM["Email опционально"]
  end

  GS --> SC
  SC --> REPO
  REPO -->|"diff"| N
  N --> TG
  N --> EM
```

## Процесс пользователя

```mermaid
flowchart LR
  A["Утро / пятница"] --> B["Сообщение:\nобъект, веха, было/стало"]
  B --> C["Открыл таблицу\nпри необходимости"]
  C --> D["Действие по сроку"]
```

## Процессы администратора

```mermaid
flowchart TD
  R1["Новый chat_id"] --> R2["Whitelist / access.db"]
  R2 --> R3["Перезапуск unit"]
  R3 --> R4["journalctl -f"]
```
