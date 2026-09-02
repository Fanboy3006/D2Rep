//! Minimal runtime binding to the official SQLite3 DLL (`sqlite3.dll`).
//!
//! Why not `rusqlite`: this environment has no C compiler, and `rusqlite` with
//! the `bundled` feature compiles the SQLite amalgamation through `cc`. The
//! official prebuilt `sqlite3.dll` (sqlite.org, x64) instead needs nothing but
//! the Windows loader: we `LoadLibrary` it at runtime and call the documented
//! C API through function pointers. No new crate dependency is introduced.
//!
//! DLL resolution order (first hit wins):
//!   1. `$DOTA_PARSE_SQLITE_DLL`
//!   2. `<dir of the running exe>/sqlite3.dll`
//!   3. `<current working dir>/sqlite3.dll`
//!   4. bare `sqlite3.dll` (OS default search path)
//!
//! The binding is deliberately tiny — just the calls this parser needs. The
//! public surface is [`Db`] (schema exec + prepared statements + scalar
//! queries) and [`PreparedWriter`] (transactional, idempotent inserts for the
//! three generic tables of §6.2).

use anyhow::{bail, Result};
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::path::{Path, PathBuf};

pub const SQLITE_OK: c_int = 0;
pub const SQLITE_ROW: c_int = 100;
pub const SQLITE_DONE: c_int = 101;

const SQLITE_OPEN_READWRITE: c_int = 0x0002;
const SQLITE_OPEN_CREATE: c_int = 0x0004;

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn LoadLibraryW(name: *const u16) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
}

type Destructor = unsafe extern "C" fn(*mut c_void);
/// `SQLITE_TRANSIENT`: the value is copied by SQLite during `bind_text`. The C
/// API spells this sentinel as the function-pointer-typed constant `-1`; it
/// cannot be built as a `const` in Rust (invalid fn-pointer value), so we build
/// it per call.
fn sqlite_transient() -> Option<Destructor> {
    unsafe { std::mem::transmute_copy(&(-1isize)) }
}

type FnOpenV2 = unsafe extern "C" fn(*const c_char, *mut *mut c_void, c_int, *const c_char) -> c_int;
type FnExec = unsafe extern "C" fn(
    *mut c_void,
    *const c_char,
    Option<unsafe extern "C" fn(*mut c_void, c_int, *mut *mut c_char, *mut *mut c_char) -> c_int>,
    *mut c_void,
    *mut *mut c_char,
) -> c_int;
type FnPrepareV2 =
    unsafe extern "C" fn(*mut c_void, *const c_char, c_int, *mut *mut c_void, *mut *const c_char) -> c_int;
type FnStep = unsafe extern "C" fn(*mut c_void) -> c_int;
type FnReset = unsafe extern "C" fn(*mut c_void) -> c_int;
type FnFinalize = unsafe extern "C" fn(*mut c_void) -> c_int;
type FnClose = unsafe extern "C" fn(*mut c_void) -> c_int;
type FnBindInt = unsafe extern "C" fn(*mut c_void, c_int, i64) -> c_int;
type FnBindDouble = unsafe extern "C" fn(*mut c_void, c_int, f64) -> c_int;
type FnBindNull = unsafe extern "C" fn(*mut c_void, c_int) -> c_int;
type FnBindText =
    unsafe extern "C" fn(*mut c_void, c_int, *const c_char, c_int, Option<Destructor>) -> c_int;
type FnErrmsg = unsafe extern "C" fn(*mut c_void) -> *const c_char;
type FnFree = unsafe extern "C" fn(*mut c_void);
type FnColumnInt = unsafe extern "C" fn(*mut c_void, c_int) -> i64;
type FnColumnDouble = unsafe extern "C" fn(*mut c_void, c_int) -> f64;
type FnColumnText = unsafe extern "C" fn(*mut c_void, c_int) -> *const u8;
type FnColumnBytes = unsafe extern "C" fn(*mut c_void, c_int) -> c_int;

struct Lib {
    _module: *mut c_void,
    open_v2: FnOpenV2,
    exec: FnExec,
    prepare_v2: FnPrepareV2,
    step: FnStep,
    reset: FnReset,
    finalize: FnFinalize,
    close: FnClose,
    bind_int64: FnBindInt,
    bind_double: FnBindDouble,
    bind_null: FnBindNull,
    bind_text: FnBindText,
    errmsg: FnErrmsg,
    free: FnFree,
    column_int64: FnColumnInt,
    column_double: FnColumnDouble,
    column_text: FnColumnText,
    column_bytes: FnColumnBytes,
}

unsafe fn symbol<T: Copy>(module: *mut c_void, name: &str) -> Result<T> {
    let cname = CString::new(name)?;
    let p = GetProcAddress(module, cname.as_ptr() as *const u8);
    if p.is_null() {
        bail!("sqlite3.dll does not export '{name}'");
    }
    Ok(std::mem::transmute_copy(&p))
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Candidate locations for `sqlite3.dll`.
fn dll_candidates() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(p) = std::env::var("DOTA_PARSE_SQLITE_DLL") {
        v.push(PathBuf::from(p));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            v.push(dir.join("sqlite3.dll"));
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        v.push(cwd.join("sqlite3.dll"));
        v.push(cwd.join("third_party").join("sqlite3.dll"));
    }
    v.push(PathBuf::from("sqlite3.dll")); // OS default search
    v
}

unsafe fn load_lib() -> Result<Lib> {
    let mut module: *mut c_void = std::ptr::null_mut();
    let mut used = String::new();
    for cand in dll_candidates() {
        let w = wide(&cand.to_string_lossy());
        module = LoadLibraryW(w.as_ptr());
        if !module.is_null() {
            used = cand.to_string_lossy().into_owned();
            break;
        }
    }
    if module.is_null() {
        bail!(
            "could not load sqlite3.dll (set DOTA_PARSE_SQLITE_DLL to its path, \
             or place it next to the executable / in the working directory)"
        );
    }
    let lib = Lib {
        _module: module,
        open_v2: symbol(module, "sqlite3_open_v2")?,
        exec: symbol(module, "sqlite3_exec")?,
        prepare_v2: symbol(module, "sqlite3_prepare_v2")?,
        step: symbol(module, "sqlite3_step")?,
        reset: symbol(module, "sqlite3_reset")?,
        finalize: symbol(module, "sqlite3_finalize")?,
        close: symbol(module, "sqlite3_close")?,
        bind_int64: symbol(module, "sqlite3_bind_int64")?,
        bind_double: symbol(module, "sqlite3_bind_double")?,
        bind_null: symbol(module, "sqlite3_bind_null")?,
        bind_text: symbol(module, "sqlite3_bind_text")?,
        errmsg: symbol(module, "sqlite3_errmsg")?,
        free: symbol(module, "sqlite3_free")?,
        column_int64: symbol(module, "sqlite3_column_int64")?,
        column_double: symbol(module, "sqlite3_column_double")?,
        column_text: symbol(module, "sqlite3_column_text")?,
        column_bytes: symbol(module, "sqlite3_column_bytes")?,
    };
    println!("[sqlite] loaded {used}");
    Ok(lib)
}

/// An open SQLite database.
pub struct Db {
    lib: Lib,
    ptr: *mut c_void,
}

impl Db {
    /// Open (creating if needed) a SQLite database file.
    pub fn open(path: &Path) -> Result<Db> {
        unsafe {
            let lib = load_lib()?;
            let cpath = CString::new(path.to_string_lossy().as_bytes())?;
            let mut db: *mut c_void = std::ptr::null_mut();
            let rc = (lib.open_v2)(
                cpath.as_ptr(),
                &mut db,
                SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE,
                std::ptr::null(),
            );
            if rc != SQLITE_OK {
                let msg = if db.is_null() {
                    String::new()
                } else {
                    db_err_text(&lib, db)
                };
                if !db.is_null() {
                    (lib.close)(db);
                }
                bail!("sqlite3_open_v2 failed ({rc}): {msg}");
            }
            Ok(Db { lib, ptr: db })
        }
    }

    /// Current error message of the connection.
    pub fn err(&self) -> String {
        unsafe { db_err_text(&self.lib, self.ptr) }
    }

    /// Run one or more SQL statements (used for schema DDL and BEGIN/COMMIT).
    pub fn exec(&self, sql: &str) -> Result<()> {
        let csql = CString::new(sql)?;
        unsafe {
            let mut errbuf: *mut c_char = std::ptr::null_mut();
            let rc = (self.lib.exec)(
                self.ptr,
                csql.as_ptr(),
                None,
                std::ptr::null_mut(),
                &mut errbuf,
            );
            if rc != SQLITE_OK {
                let msg = if errbuf.is_null() {
                    self.err()
                } else {
                    CStr::from_ptr(errbuf).to_string_lossy().into_owned()
                };
                if !errbuf.is_null() {
                    (self.lib.free)(errbuf as *mut c_void);
                }
                bail!("sqlite3_exec failed ({rc}): {msg}");
            }
        }
        Ok(())
    }

    /// Prepare a statement for repeated execution.
    pub fn prepare(&self, sql: &str) -> Result<Statement<'_>> {
        let csql = CString::new(sql)?;
        unsafe {
            let mut stmt: *mut c_void = std::ptr::null_mut();
            let rc =
                (self.lib.prepare_v2)(self.ptr, csql.as_ptr(), -1, &mut stmt, std::ptr::null_mut());
            if rc != SQLITE_OK {
                bail!("sqlite3_prepare_v2 failed ({rc}): {}", self.err());
            }
            Ok(Statement {
                db: self,
                ptr: stmt,
            })
        }
    }
}

unsafe fn db_err_text(lib: &Lib, db: *mut c_void) -> String {
    let p = (lib.errmsg)(db);
    if p.is_null() {
        String::from("unknown sqlite error")
    } else {
        CStr::from_ptr(p).to_string_lossy().into_owned()
    }
}

impl Drop for Db {
    fn drop(&mut self) {
        unsafe {
            if !self.ptr.is_null() {
                (self.lib.close)(self.ptr);
                self.ptr = std::ptr::null_mut();
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Row,
    Done,
}

/// A prepared statement bound to a database connection.
pub struct Statement<'db> {
    db: &'db Db,
    ptr: *mut c_void,
}

impl Statement<'_> {
    /// Reset so the statement can be re-bound and stepped again.
    fn reset(&self) -> Result<()> {
        unsafe {
            let rc = (self.db.lib.reset)(self.ptr);
            if rc != SQLITE_OK {
                bail!("sqlite3_reset failed ({rc}): {}", self.db.err());
            }
        }
        Ok(())
    }

    pub fn bind_int64(&self, idx: c_int, v: i64) -> Result<()> {
        self.reset()?;
        unsafe {
            let rc = (self.db.lib.bind_int64)(self.ptr, idx, v);
            if rc != SQLITE_OK {
                bail!("sqlite3_bind_int64 failed ({rc}): {}", self.db.err());
            }
        }
        Ok(())
    }

    pub fn bind_double(&self, idx: c_int, v: f64) -> Result<()> {
        self.reset()?;
        unsafe {
            let rc = (self.db.lib.bind_double)(self.ptr, idx, v);
            if rc != SQLITE_OK {
                bail!("sqlite3_bind_double failed ({rc}): {}", self.db.err());
            }
        }
        Ok(())
    }

    pub fn bind_null(&self, idx: c_int) -> Result<()> {
        self.reset()?;
        unsafe {
            let rc = (self.db.lib.bind_null)(self.ptr, idx);
            if rc != SQLITE_OK {
                bail!("sqlite3_bind_null failed ({rc}): {}", self.db.err());
            }
        }
        Ok(())
    }

    pub fn bind_text(&self, idx: c_int, text: &str) -> Result<()> {
        self.reset()?;
        unsafe {
            let rc = (self.db.lib.bind_text)(
                self.ptr,
                idx,
                text.as_ptr() as *const c_char,
                text.len() as c_int,
                sqlite_transient(),
            );
            if rc != SQLITE_OK {
                bail!("sqlite3_bind_text failed ({rc}): {}", self.db.err());
            }
        }
        Ok(())
    }

    /// Advance the statement. `Row` is returned for SELECT result rows.
    pub fn step(&self) -> Result<Step> {
        unsafe {
            match (self.db.lib.step)(self.ptr) {
                SQLITE_ROW => Ok(Step::Row),
                SQLITE_DONE => Ok(Step::Done),
                rc => bail!("sqlite3_step failed ({rc}): {}", self.db.err()),
            }
        }
    }

    pub fn column_i64(&self, col: c_int) -> i64 {
        unsafe { (self.db.lib.column_int64)(self.ptr, col) }
    }

    pub fn column_f64(&self, col: c_int) -> f64 {
        unsafe { (self.db.lib.column_double)(self.ptr, col) }
    }

    /// Column value as UTF-8 lossy string (works for TEXT and numeric columns).
    pub fn column_str(&self, col: c_int) -> String {
        unsafe {
            let n = (self.db.lib.column_bytes)(self.ptr, col);
            let p = (self.db.lib.column_text)(self.ptr, col);
            if p.is_null() {
                return String::new();
            }
            let bytes = std::slice::from_raw_parts(p, n.max(0) as usize);
            String::from_utf8_lossy(bytes).into_owned()
        }
    }
}

impl Drop for Statement<'_> {
    fn drop(&mut self) {
        unsafe {
            if !self.ptr.is_null() {
                (self.db.lib.finalize)(self.ptr);
                self.ptr = std::ptr::null_mut();
            }
        }
    }
}

/// Row counts written by a [`PreparedWriter`], indexed like `[snapshots,
/// events, players]`.
#[derive(Debug, Clone, Copy, Default)]
pub struct WriteStats {
    pub snapshots: u64,
    pub events: u64,
    pub players: u64,
}

/// Transactional writer for the three §6.2 tables.
///
/// Idempotency contract: rows of the target `match_id` are deleted from all
/// three tables first, then new rows are inserted, all inside one transaction.
pub struct PreparedWriter<'a> {
    db: &'a Db,
    ins_snapshot: Statement<'a>,
    ins_event: Statement<'a>,
    ins_player: Statement<'a>,
    stats: WriteStats,
}

impl<'a> PreparedWriter<'a> {
    pub fn begin(db: &'a Db, match_id: i64) -> Result<PreparedWriter<'a>> {
        db.exec("BEGIN")?;
        // Delete any previously parsed rows for this match (idempotent
        // re-parse). The statements are finalised when they go out of scope.
        {
            let del_snapshot = db.prepare("DELETE FROM entity_snapshots WHERE match_id = ?1")?;
            let del_event = db.prepare("DELETE FROM game_events WHERE match_id = ?1")?;
            let del_player = db.prepare("DELETE FROM player_identity WHERE match_id = ?1")?;
            for del in [&del_snapshot, &del_event, &del_player] {
                del.bind_int64(1, match_id)?;
                if del.step()? != Step::Done {
                    bail!("unexpected row from DELETE statement");
                }
            }
        }
        let ins_snapshot = db.prepare(
            "INSERT INTO entity_snapshots
                 (match_id, game_time_sec, entity_type, entity_id, team, x, y, hp, extra)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        )?;
        let ins_event = db.prepare(
            "INSERT INTO game_events
                 (match_id, game_time_sec, event_type, actor_id, target_id, x, y, properties, event_seq)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        )?;
        let ins_player = db.prepare(
            "INSERT INTO player_identity
                 (match_id, player_slot, steam_id, player_name, hero_name, hero_id, team_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        )?;
        Ok(PreparedWriter {
            db,
            ins_snapshot,
            ins_event,
            ins_player,
            stats: WriteStats::default(),
        })
    }

    pub fn insert_snapshot(
        &mut self,
        match_id: i64,
        row: &crate::model::SnapshotRow,
    ) -> Result<()> {
        let s = &self.ins_snapshot;
        s.bind_int64(1, match_id)?;
        s.bind_int64(2, row.game_time_sec)?;
        s.bind_text(3, row.entity_type)?;
        s.bind_text(4, &row.entity_id)?;
        match &row.team {
            Some(t) => s.bind_text(5, t)?,
            None => s.bind_null(5)?,
        }
        s.bind_double(6, row.x)?;
        s.bind_double(7, row.y)?;
        match row.hp {
            Some(h) => s.bind_int64(8, h)?,
            None => s.bind_null(8)?,
        }
        s.bind_text(9, &row.extra.to_string())?;
        self.step_done(&s, "entity_snapshots")?;
        self.stats.snapshots += 1;
        Ok(())
    }

    pub fn insert_event(&mut self, match_id: i64, row: &crate::model::EventRow) -> Result<()> {
        let s = &self.ins_event;
        s.bind_int64(1, match_id)?;
        s.bind_int64(2, row.game_time_sec)?;
        s.bind_text(3, row.event_type)?;
        match &row.actor_id {
            Some(a) => s.bind_text(4, a)?,
            None => s.bind_null(4)?,
        }
        match &row.target_id {
            Some(t) => s.bind_text(5, t)?,
            None => s.bind_null(5)?,
        }
        match row.x {
            Some(v) => s.bind_double(6, v)?,
            None => s.bind_null(6)?,
        }
        match row.y {
            Some(v) => s.bind_double(7, v)?,
            None => s.bind_null(7)?,
        }
        s.bind_text(8, &row.properties.to_string())?;
        s.bind_int64(9, row.event_seq)?;
        self.step_done(&s, "game_events")?;
        self.stats.events += 1;
        Ok(())
    }

    pub fn insert_player(
        &mut self,
        match_id: i64,
        row: &crate::model::PlayerIdentityRow,
    ) -> Result<()> {
        let s = &self.ins_player;
        s.bind_int64(1, match_id)?;
        s.bind_int64(2, row.player_slot)?;
        match row.steam_id {
            Some(v) => s.bind_int64(3, v)?,
            None => s.bind_null(3)?,
        }
        s.bind_text(4, &row.player_name)?;
        s.bind_text(5, &row.hero_name)?;
        match row.hero_id {
            Some(v) => s.bind_int64(6, v)?,
            None => s.bind_null(6)?,
        }
        match row.team_id {
            Some(v) => s.bind_int64(7, v)?,
            None => s.bind_null(7)?,
        }
        self.step_done(&s, "player_identity")?;
        self.stats.players += 1;
        Ok(())
    }

    fn step_done(&self, s: &Statement<'_>, table: &str) -> Result<()> {
        match s.step()? {
            Step::Done => Ok(()),
            Step::Row => bail!("unexpected row returned while inserting into {table}"),
        }
    }

    /// Commit the transaction.
    pub fn commit(&mut self) -> Result<WriteStats> {
        self.db.exec("COMMIT")?;
        Ok(self.stats)
    }
}
