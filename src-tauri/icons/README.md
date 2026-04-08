# Icons (TODO)

Tauri требует набор иконок под Windows-бандл. Здесь должны лежать **реальные**
бинарные файлы. Сейчас директория пустая — первый `tauri build` упадёт пока
файлы не появятся.

## Что нужно положить

| Файл | Размер | Назначение |
|------|--------|-----------|
| `32x32.png` | 32×32 | tray / маленькая иконка окна |
| `128x128.png` | 128×128 | окно |
| `128x128@2x.png` | 256×256 | HiDPI окно |
| `icon.ico` | multi-res (16/32/48/256) | NSIS installer + .exe |
| `icon.png` | 512×512 (или больше) | fallback / Linux |

## Как сгенерировать

Самый простой способ — взять один большой PNG (1024×1024, прозрачный фон) и
прогнать через `tauri icon`:

```bash
# на машине где есть Node + Tauri CLI (например, в CI на windows-latest):
npx @tauri-apps/cli icon path/to/source-1024.png --output src-tauri/icons
```

Команда сама сгенерит весь набор (включая `.ico` и `.icns`).

Альтернатива без Tauri CLI: ImageMagick
```bash
convert source.png -resize 32x32 32x32.png
convert source.png -resize 128x128 128x128.png
convert source.png -resize 256x256 128x128@2x.png
convert source.png -define icon:auto-resize=16,32,48,256 icon.ico
cp source.png icon.png
```

## Где взять исходник

TODO: Дима/Настя дают логотип проекта — кладём `source-1024.png` рядом с этим
README, гоняем `tauri icon`, коммитим результат, исходник в `.gitignore`.
