"""Provision or revoke a gateway API key, and grant exact tool permissions.

The raw key is printed once by `issue` and is not logged. Startup of the
gateway service does not create or print a key. A new key cannot call any
tool until `grant` adds one exact public tool name.

    python -m apps.gateway.provision issue
    python -m apps.gateway.provision issue --output ABSOLUTE_PATH [--grant EXACT_TOOL_NAME]
    python -m apps.gateway.provision revoke <key_id>
    python -m apps.gateway.provision grant <key_id> <tool_name>
    python -m apps.gateway.provision revoke-permission <key_id> <tool_name>
    python -m apps.gateway.provision create-admin USERNAME --output ABSOLUTE_PATH
    python -m apps.gateway.provision reset-admin-password --user-id USER_ID --username USERNAME --output ABSOLUTE_PATH

`--output` writes the raw key, or the initial administrator password, to a new
0600 file and does not print it.
The database URL stays in the environment.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

from apps.gateway.app.keys import KEY_ID_PATTERN, StoreUnavailable
from apps.gateway.app.passwords import generate_password, hash_password
from apps.gateway.app.permissions import is_permission_name
from apps.gateway.app.store import PostgresStore

_USERNAME = re.compile(r"[a-z][a-z0-9_-]{2,31}")

_REPO_ROOT = Path(__file__).resolve().parents[2]


class _SafeFailure(Exception):
    """Operational failure whose message is fixed and contains no key material."""


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "issue":
        output, grant_name = _parse_issue_args(args[1:])
        asyncio.run(_issue(output, grant_name))
        return
    if len(args) == 2 and args[0] == "revoke":
        asyncio.run(_revoke(args[1]))
        return
    if len(args) == 3 and args[0] == "grant":
        asyncio.run(_grant(args[1], args[2]))
        return
    if len(args) == 3 and args[0] == "revoke-permission":
        asyncio.run(_revoke_permission(args[1], args[2]))
        return
    if len(args) == 4 and args[0] == "create-admin" and args[2] == "--output":
        asyncio.run(_create_admin(args[1], args[3]))
        return
    if args and args[0] == "reset-admin-password":
        user_id, username, output = _parse_reset_args(args[1:])
        asyncio.run(_reset_admin_password(user_id, username, output))
        return
    _usage()


def _usage() -> None:
    print(
        "Usage: python -m apps.gateway.provision issue [--output ABSOLUTE_PATH "
        "[--grant EXACT_TOOL_NAME]] | revoke <key_id> "
        "| grant <key_id> <tool_name> | revoke-permission <key_id> <tool_name> "
        "| create-admin USERNAME --output ABSOLUTE_PATH "
        "| reset-admin-password --user-id USER_ID --username USERNAME --output ABSOLUTE_PATH",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _parse_issue_args(args: list[str]) -> tuple[str | None, str | None]:
    if not args:
        return None, None
    output: str | None = None
    grant_name: str | None = None
    index = 0
    while index < len(args):
        flag = args[index]
        if flag not in {"--output", "--grant"} or index + 1 >= len(args):
            _usage()
        value = args[index + 1]
        if flag == "--output":
            if output is not None:
                _usage()
            output = value
        else:
            if grant_name is not None:
                _usage()
            grant_name = value
        index += 2
    if grant_name is not None and output is None:
        print("--grant requires --output.", file=sys.stderr)
        raise SystemExit(2)
    return output, grant_name


async def _issue(output: str | None, grant_name: str | None) -> None:
    if grant_name is not None and not is_permission_name(grant_name):
        print("No permission was granted.", file=sys.stderr)
        raise SystemExit(1)
    destination = _checked_output(output) if output is not None else None
    store = _store()
    exit_code: int | None = None
    try:
        if destination is None:
            await _issue_stdout(store)
        else:
            await _issue_to_file(store, destination, grant_name)
    except SystemExit as exc:
        code = exc.code
        exit_code = code if isinstance(code, int) else 1
    finally:
        try:
            await store.close()
        except Exception:
            print("Could not close the key store.", file=sys.stderr)
            if exit_code is None:
                exit_code = 1
    if exit_code not in (None, 0):
        raise SystemExit(exit_code)


async def _issue_stdout(store: PostgresStore) -> None:
    try:
        await store.open()
        issued = await store.issue()
    except StoreUnavailable:
        print("Could not reach the gateway database.", file=sys.stderr)
        raise SystemExit(1) from None
    print("Gateway API key created.")
    print(f"key_id: {issued.key_id}")
    print(f"gateway_api_key: {issued.raw_key}")
    print("This key is shown once. It is not stored and will not be printed again.")


async def _issue_to_file(store: PostgresStore, destination: Path, grant_name: str | None) -> None:
    fd: int | None = None
    created = False
    key_id: str | None = None
    try:
        await store.open()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(destination, flags, 0o600)
        created = True
        os.fchmod(fd, 0o600)
        issued = await store.issue()
        key_id = issued.key_id
        _write_all(fd, (issued.raw_key + "\n").encode("utf-8"))
        os.close(fd)
        fd = None
        if grant_name is not None:
            granted = await store.grant(key_id, grant_name)
            if not granted:
                raise _SafeFailure("No permission was granted.")
        print("Gateway API key created.")
        print(f"key_id: {key_id}")
        if grant_name is not None:
            print(f"Granted {grant_name} to {key_id}.")
    except _SafeFailure as exc:
        _close_fd(fd)
        fd = None
        await _rollback(store, key_id, destination, created)
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except StoreUnavailable:
        _close_fd(fd)
        fd = None
        await _rollback(store, key_id, destination, created)
        print("Could not reach the gateway database.", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        _close_fd(fd)
        fd = None
        await _rollback(store, key_id, destination, created)
        print("Could not write the key file.", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        _close_fd(fd)
        fd = None
        await _rollback(store, key_id, destination, created)
        print("Could not finish issuing the key.", file=sys.stderr)
        raise SystemExit(1) from None


def _checked_output(text: str) -> Path:
    path = Path(text)
    if not path.is_absolute():
        print("Output path must be absolute.", file=sys.stderr)
        raise SystemExit(1)
    if path.is_symlink():
        print("Output path must not be a symlink.", file=sys.stderr)
        raise SystemExit(1)
    if path.exists():
        print("Output path already exists.", file=sys.stderr)
        raise SystemExit(1)
    parent = path.parent
    if not parent.is_dir():
        print("Output directory does not exist.", file=sys.stderr)
        raise SystemExit(1)
    repo = str(_REPO_ROOT)
    parent_real = str(parent.resolve())
    if parent_real == repo or parent_real.startswith(repo + os.sep):
        print("Output path must be outside the repository.", file=sys.stderr)
        raise SystemExit(1)
    return path


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(0, "Could not write the key file.")
        view = view[written:]
    os.fsync(fd)


def _close_fd(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


async def _rollback(store: PostgresStore, key_id: str | None, path: Path, created: bool) -> None:
    need_revoke = key_id is not None
    revoked = not need_revoke
    if need_revoke:
        try:
            revoked = bool(await store.revoke(key_id))
        except Exception:
            revoked = False
    removed = not created
    if created:
        try:
            os.unlink(path)
            removed = True
        except FileNotFoundError:
            removed = True
        except OSError:
            removed = False
    if need_revoke and created and revoked and removed:
        print("The new key was revoked and the new file was removed.", file=sys.stderr)
        return
    if need_revoke and revoked and not created:
        print("The new key was revoked.", file=sys.stderr)
    elif need_revoke and not revoked:
        print("Could not revoke the new key.", file=sys.stderr)
    if created and removed and not (need_revoke and revoked):
        print("The new file was removed.", file=sys.stderr)
    elif created and not removed:
        print("Could not remove the new key file.", file=sys.stderr)


async def _revoke(key_id: str) -> None:
    if KEY_ID_PATTERN.fullmatch(key_id) is None:
        print("No key was revoked.", file=sys.stderr)
        raise SystemExit(1)
    store = _store()
    try:
        await store.open()
        revoked = await store.revoke(key_id)
    except StoreUnavailable:
        print("Could not reach the gateway database.", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        await store.close()
    if not revoked:
        print("No key was revoked.", file=sys.stderr)
        raise SystemExit(1)
    print(f"Revoked key {key_id}.")


async def _grant(key_id: str, tool_name: str) -> None:
    if not _permission_args(key_id, tool_name):
        print("No permission was granted.", file=sys.stderr)
        raise SystemExit(1)
    store = _store()
    try:
        await store.open()
        granted = await store.grant(key_id, tool_name)
    except StoreUnavailable:
        print("Could not reach the gateway database.", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        await store.close()
    if not granted:
        print("No permission was granted.", file=sys.stderr)
        raise SystemExit(1)
    print(f"Granted {tool_name} to {key_id}.")


async def _revoke_permission(key_id: str, tool_name: str) -> None:
    if not _permission_args(key_id, tool_name):
        print("No permission was removed.", file=sys.stderr)
        raise SystemExit(1)
    store = _store()
    try:
        await store.open()
        removed = await store.revoke_permission(key_id, tool_name)
    except StoreUnavailable:
        print("Could not reach the gateway database.", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        await store.close()
    if not removed:
        print("No permission was removed.", file=sys.stderr)
        raise SystemExit(1)
    print(f"Removed {tool_name} from {key_id}.")


def _permission_args(key_id: str, tool_name: str) -> bool:
    return KEY_ID_PATTERN.fullmatch(key_id) is not None and is_permission_name(tool_name)


async def _create_admin(username: str, output: str) -> None:
    if _USERNAME.fullmatch(username) is None:
        print("Could not create the administrator.", file=sys.stderr)
        raise SystemExit(1)
    destination = _checked_output(output)
    store = _store()
    exit_code: int | None = None
    try:
        await _write_admin(store, username, destination)
    except SystemExit as exc:
        code = exc.code
        exit_code = code if isinstance(code, int) else 1
    finally:
        try:
            await store.close()
        except Exception:
            print("Could not close the key store.", file=sys.stderr)
            if exit_code is None:
                exit_code = 1
    if exit_code not in (None, 0):
        raise SystemExit(exit_code)


async def _write_admin(store: PostgresStore, username: str, destination: Path) -> None:
    fd: int | None = None
    created = False
    try:
        await store.open()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(destination, flags, 0o600)
        created = True
        os.fchmod(fd, 0o600)
        password = generate_password()
        user = await store.create_user(username, hash_password(password), "admin")
        _write_all(fd, (password + "\n").encode("utf-8"))
        os.close(fd)
        fd = None
        print("Gateway administrator created.")
        print(f"username: {user.username}")
        print(f"user_id: {user.user_id}")
        print("The initial password was written to the output file and is not printed.")
    except StoreUnavailable:
        _close_fd(fd)
        if created:
            try:
                os.unlink(destination)
            except OSError:
                print("Could not remove the new password file.", file=sys.stderr)
        print("Could not create the administrator.", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        _close_fd(fd)
        if created:
            try:
                os.unlink(destination)
            except OSError:
                print("Could not remove the new password file.", file=sys.stderr)
        print("Could not write the password file.", file=sys.stderr)
        raise SystemExit(1) from None


def _parse_reset_args(args: list[str]) -> tuple[str, str, str]:
    if len(args) != 6:
        _usage()
    values = {args[0]: args[1], args[2]: args[3], args[4]: args[5]}
    if set(values) != {"--user-id", "--username", "--output"}:
        _usage()
    return values["--user-id"], values["--username"], values["--output"]


async def _reset_admin_password(user_id: str, username: str, output: str) -> None:
    if KEY_ID_PATTERN.fullmatch(user_id) is None or _USERNAME.fullmatch(username) is None:
        print("Could not reset the administrator password.", file=sys.stderr)
        raise SystemExit(1)
    destination = _checked_output(output)
    store = _store()
    exit_code: int | None = None
    try:
        await _reset_admin_file(store, user_id, username, destination)
    except SystemExit as exc:
        code = exc.code
        exit_code = code if isinstance(code, int) else 1
    finally:
        try:
            await store.close()
        except Exception:
            print("Could not close the key store.", file=sys.stderr)
            if exit_code is None:
                exit_code = 1
    if exit_code not in (None, 0):
        raise SystemExit(exit_code)


async def _reset_admin_file(store: PostgresStore, user_id: str, username: str, destination: Path) -> None:
    fd: int | None = None
    created = False
    started = False
    try:
        await store.open(ensure_schema=False)
        password = generate_password()
        verifier = hash_password(password)
        async with store.reset_admin_password(user_id, username, verifier) as user:
            started = True
            if user.user_id != user_id or user.username != username or user.role != "admin":
                raise StoreUnavailable("Gateway administrator was not updated.")
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(destination, flags, 0o600)
            created = True
            os.fchmod(fd, 0o600)
            _write_all(fd, (password + "\n").encode("utf-8"))
            os.close(fd)
            fd = None
            _fsync_directory(destination.parent)
    except StoreUnavailable:
        _close_fd(fd)
        _finish_failed_reset(destination, created, wrote=False)
        raise SystemExit(1) from None
    except OSError:
        _close_fd(fd)
        _finish_failed_reset(destination, created, wrote=True)
        raise SystemExit(1) from None
    except Exception:
        _close_fd(fd)
        _finish_failed_reset(destination, created, wrote=True)
        raise SystemExit(1) from None
    except BaseException:
        _close_fd(fd)
        _finish_failed_reset(destination, created, wrote=started)
        raise
    print("Gateway administrator password reset.")
    print(f"username: {user.username}")
    print(f"user_id: {user.user_id}")
    print("The new password was written to the output file and is not printed.")


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _finish_failed_reset(path: Path, created: bool, *, wrote: bool) -> None:
    removed = _remove_created_file(path, created)
    if wrote:
        print("Could not write the password file.", file=sys.stderr)
    else:
        print("Could not reset the administrator password.", file=sys.stderr)
    if not created:
        if wrote:
            print("The password change was rolled back.", file=sys.stderr)
        return
    if removed:
        print("The password change was rolled back.", file=sys.stderr)
        print("The new password file was removed.", file=sys.stderr)
        return
    print("Could not remove the new password file.", file=sys.stderr)
    print("Could not confirm the password change was rolled back.", file=sys.stderr)


def _remove_created_file(path: Path, created: bool) -> bool:
    if not created:
        return True
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _store() -> PostgresStore:
    url = os.environ.get("GATEWAY_DATABASE_URL", "").strip()
    if not url:
        print("Set GATEWAY_DATABASE_URL before provisioning a gateway API key.", file=sys.stderr)
        raise SystemExit(1)
    return PostgresStore(url)


if __name__ == "__main__":
    main()
