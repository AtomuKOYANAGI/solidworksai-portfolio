# 構成と出典

元の個人開発リポジトリ `AtomuKOYANAGI/solidworksai` のスナップショット
`969222c577e86bd98e5947673d1571b5c8bf9816` から、公開用に小さく整理しました。
元リポジトリは非公開です。このSHAは取得したスナップショットを示し、収録した過去のCAD実行時のSHAとは限りません。

## そのまま抜粋したもの

`swai_demo/` 内の `parser.py`、`controlled_parser.py`、`schema.py`、`spec_tools.py`、`errors.py`、`design_spec.schema.json` は元の `project/fast_mvp/` から内容を変更せずにコピーしています。相対インポートはそのまま利用しています。

元の `__init__.py` はCAD実行パイプラインも読み込むため、公開版では専用の入口に置き換えています。`demo.py`、`__main__.py`、公開デモ用テスト・説明・模式図は公開用に追加しました。これらの整理・追加にもAI支援を利用しています。

元のモジュールには公開CLI以外の形状処理も含まれます。CLIでは対象を板と左右対称の2穴に限定しています。公開テストの通過は、元プロジェクトの全機能の検証を意味しません。

## 実行記録

`examples/plate_ja.txt`、`evidence/plate/design_spec.json`、`model.py`、`step_validation.json` は、元の `artifacts/latest/plate-two-holes-04778f5c73de/` からの抜粋です。`step_validation.json` の元ファイル名は `validation.json` です。

`solidworks_summary.json` は、同じ場所の `solidworks_validation.json` と `run_summary.json` から必要な数値を抽出した要約です。元の値は変更していません。公開版の作成時にCADを再実行した記録ではありません。

`model.py` は固定テンプレートから生成された、読み物としてのCADコード例です。公開デモからは呼び出しません。過去の実行環境では `build123d 0.11.1` を使用しています。

## 再現性

[provenance.json](provenance.json) に、抜粋ファイルの元のパス・Git blob SHA・公開ファイルのSHA-256を記載しています。テストで内容の一致を確認します。要約ファイルは、参照した元ファイルの情報を自身の `sources` に保持しています。

実行に必要な依存関係は `requirements.txt` に固定しています。デモとテストでは外部モデル・SOLIDWORKS・build123dを使用しません。
