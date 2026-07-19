;;; Lightweight, version-aware loader for the automatic dimension engine.
;;; The dispatcher may load this tiny file for every annotation IPC request, but
;;; the planning engine and ActiveX commit override are parsed only once per
;;; AutoCAD document/version.

(setq mcp-ad-loader-target-version "phase2-2026-07-19")

(if
  (or
    (not (boundp '*mcp-auto-dimension-loader-version*))
    (/= *mcp-auto-dimension-loader-version* mcp-ad-loader-target-version)
  )
  (progn
    (setq mcp-ad-engine-path (findfile "auto_dimension.lsp"))
    (if (not mcp-ad-engine-path)
      (error "auto_dimension.lsp was not found in AutoCAD Support File Search Path")
    )
    (setq mcp-ad-activex-path (findfile "auto_dimension_activex.lsp"))
    (if (not mcp-ad-activex-path)
      (error "auto_dimension_activex.lsp was not found in AutoCAD Support File Search Path")
    )
    (load mcp-ad-engine-path)
    ;; Loaded second so it replaces only the final mutation/commit entry point.
    (load mcp-ad-activex-path)
    (setq *mcp-auto-dimension-loader-version* mcp-ad-loader-target-version)
  )
)

(princ)
