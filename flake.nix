{
  description = "Certora Blockchain Risk Assessment Dashboard";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs";
    flake-utils.url = "github:numtide/flake-utils";
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      flake-compat,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ ];
        };

        devShell =
          with pkgs;
          mkShellNoCC {
            name = "bc-risk-assessment";
            packages = [
              python313
              temporal
              temporal-cli
              uv
              nodejs_20
              bun
              pre-commit
              jq
              awscli2
              gh
            ];

            nativeBuildInputs = [
              # set SOURCE_DATE_EPOCH so that we can use python wheels
              ensureNewerSourcesForZipFilesHook
            ];

            shellHook = ''
              unset PYTHONPATH
            '';
          };
      in
      {
        devShell = devShell;
        packages = {
          dev-shell = devShell.inputDerivation;
        };
      }
    );
}
