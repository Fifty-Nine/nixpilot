.PHONY: check fmt install-hooks

check:
	nix flake check

fmt:
	nix fmt .

install-hooks:
	nix develop . --command pre-commit -- install
	nix develop . --command pre-commit -- install --hook-type post-commit
