# Convenience: run any root Makefile target from this folder (make install, make run, make test, ...).
.DEFAULT_GOAL := help

help:
	@$(MAKE) --no-print-directory -C .. help 2>/dev/null || echo "Targets: install run test frontend (see ../Makefile)"

%:
	@$(MAKE) --no-print-directory -C .. $@
