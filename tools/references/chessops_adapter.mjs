#!/usr/bin/env node
/** Pinned chessops Crazyhouse adapter for the shared JSONL protocol. */

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

const REQUEST_SCHEMA = 'crazyhouse-reference-request/v1';
const RESPONSE_SCHEMA = 'crazyhouse-reference-response/v1';
const AUTHORITY_PROFILE = 'LICHESS_CRAZYHOUSE_2026_08_12';
const EXPECTED_COMMIT = '736c40ced7130d453d85e7979c360b797474c9a7';
const EXPECTED_TREE = 'd555da3d103eef217c7a894e7a994c4f55313a42';
const POCKET_ORDER = 'PNBRQpnbrq';

class AdapterError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const parseArgs = argv => {
  const parsed = { input: '-', output: '-' };
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!flag?.startsWith('--') || value === undefined) throw new AdapterError('INVALID_ARGUMENTS', `invalid argument ${flag ?? '<missing>'}`);
    if (flag === '--require-root') parsed.requireRoot = value;
    else if (flag === '--input') parsed.input = value;
    else if (flag === '--output') parsed.output = value;
    else throw new AdapterError('INVALID_ARGUMENTS', `unknown argument ${flag}`);
  }
  if (!parsed.requireRoot) throw new AdapterError('INVALID_ARGUMENTS', '--require-root is required');
  return parsed;
};

const sha256 = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');

const git = (root, ...args) => {
  try {
    return execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 10_000 }).trim();
  } catch (error) {
    throw new AdapterError('IDENTITY_UNAVAILABLE', `git identity probe failed: ${error.message}`);
  }
};

const authenticateCheckout = rootInput => {
  const root = fs.realpathSync(rootInput);
  const commit = git(root, 'rev-parse', 'HEAD');
  const tree = git(root, 'rev-parse', 'HEAD^{tree}');
  const dirty = git(root, 'status', '--porcelain');
  if (commit !== EXPECTED_COMMIT || tree !== EXPECTED_TREE) {
    throw new AdapterError('WRONG_REFERENCE_IDENTITY', `expected ${EXPECTED_COMMIT}/${EXPECTED_TREE}, got ${commit}/${tree}`);
  }
  if (dirty) throw new AdapterError('DIRTY_REFERENCE', 'chessops checkout is not clean');

  const packageFile = path.join(root, 'package.json');
  const moduleFile = path.join(root, 'dist', 'esm', 'variant.js');
  const packageJson = JSON.parse(fs.readFileSync(packageFile, 'utf8'));
  if (!fs.existsSync(moduleFile)) throw new AdapterError('BUILD_MISSING', `compiled module not found: ${moduleFile}`);
  return {
    root,
    identity: {
      name: 'chessops',
      version: packageJson.version,
      commit,
      tree,
      module_path: moduleFile,
      module_sha256: sha256(moduleFile),
      package_lock_sha256: sha256(path.join(root, 'package-lock.json')),
      license: packageJson.license,
      role: 'independent_differential_reference',
      result_authority: false,
    },
  };
};

const canonicalizeFen = fen => {
  const fields = fen.trim().split(/\s+/);
  if (fields.length !== 6) throw new AdapterError('INVALID_FEN', `expected six FEN fields, got ${fields.length}`);
  const boardAndPocket = fields[0];
  let board;
  let pocket = '';
  if (boardAndPocket.endsWith(']') && boardAndPocket.includes('[')) {
    const splitAt = boardAndPocket.lastIndexOf('[');
    board = boardAndPocket.slice(0, splitAt);
    pocket = boardAndPocket.slice(splitAt + 1, -1);
  } else if ((boardAndPocket.match(/\//g) ?? []).length === 8) {
    const ranks = boardAndPocket.split('/');
    board = ranks.slice(0, 8).join('/');
    pocket = ranks[8];
  } else if ((boardAndPocket.match(/\//g) ?? []).length === 7) board = boardAndPocket;
  else throw new AdapterError('INVALID_FEN', 'Crazyhouse board field has neither eight ranks nor a pocket field');

  if ((board.match(/\//g) ?? []).length !== 7) throw new AdapterError('INVALID_FEN', 'Crazyhouse board field does not contain eight ranks');
  for (const symbol of pocket) if (!POCKET_ORDER.includes(symbol)) throw new AdapterError('INVALID_FEN', 'pocket contains an unsupported piece symbol');
  const orderedPocket = [...POCKET_ORDER].map(symbol => symbol.repeat([...pocket].filter(item => item === symbol).length)).join('');
  fields[0] = `${board}[${orderedPocket}]`;
  return fields.join(' ');
};

const physicalState = canonicalFen => {
  const [boardAndPocket, turn, castling, epSquare, halfmove, fullmove] = canonicalFen.split(' ');
  const splitAt = boardAndPocket.lastIndexOf('[');
  const board = boardAndPocket.slice(0, splitAt);
  const pocket = boardAndPocket.slice(splitAt + 1, -1);
  const promoted = [];
  board.split('/').forEach((rank, rankIndex) => {
    let fileIndex = 0;
    let previousSquare = null;
    for (const char of rank) {
      if (/\d/.test(char)) {
        fileIndex += Number(char);
        previousSquare = null;
      } else if (char === '~') {
        if (previousSquare === null) throw new AdapterError('INVALID_FEN', 'promoted marker is not attached to a board piece');
        promoted.push(previousSquare);
      } else {
        if (fileIndex >= 8) throw new AdapterError('INVALID_FEN', 'rank exceeds eight files');
        previousSquare = `${String.fromCharCode('a'.charCodeAt(0) + fileIndex)}${8 - rankIndex}`;
        fileIndex += 1;
      }
    }
    if (fileIndex !== 8) throw new AdapterError('INVALID_FEN', 'rank does not contain eight files');
  });
  const roles = { pawn: 'P', knight: 'N', bishop: 'B', rook: 'R', queen: 'Q' };
  const count = symbol => [...pocket].filter(item => item === symbol).length;
  return {
    canonical_fen: canonicalFen,
    turn: turn === 'w' ? 'white' : 'black',
    castling_rights: castling,
    ep_square: epSquare === '-' ? null : epSquare,
    halfmove_clock: Number(halfmove),
    fullmove_number: Number(fullmove),
    pockets: {
      white: Object.fromEntries(Object.entries(roles).map(([role, symbol]) => [role, count(symbol)])),
      black: Object.fromEntries(Object.entries(roles).map(([role, symbol]) => [role, count(symbol.toLowerCase())])),
    },
    promoted_squares: promoted.sort(),
  };
};

const loadModules = async root => {
  const fromRoot = relative => import(pathToFileURL(path.join(root, 'dist', 'esm', relative)).href);
  const [fen, variant, util, types, squareSet, debug] = await Promise.all([
    fromRoot('fen.js'),
    fromRoot('variant.js'),
    fromRoot('util.js'),
    fromRoot('types.js'),
    fromRoot('squareSet.js'),
    fromRoot('debug.js'),
  ]);
  return { ...fen, ...variant, ...util, ...types, ...squareSet, debug };
};

const createOperations = modules => {
  const { parseFen, makeFen, Crazyhouse, parseUci, makeUci, kingCastlesTo, ROLES, SquareSet, debug } = modules;
  const promotionRoles = ['queen', 'knight', 'rook', 'bishop'];

  const parsePosition = fen => {
    try {
      return Crazyhouse.fromSetup(parseFen(fen).unwrap()).unwrap();
    } catch (error) {
      throw new AdapterError('INVALID_POSITION', error instanceof Error ? error.message : String(error));
    }
  };

  const legalMoves = position => {
    const moves = [];
    const context = position.ctx();
    for (const [from, destinations] of position.allDests(context)) {
      const rank = Math.floor(from / 8);
      const promotions = position.board.pawn.has(from) && rank === (position.turn === 'white' ? 6 : 1) ? promotionRoles : [undefined];
      for (const to of destinations) {
        let canonicalTo = to;
        if (position.board.king.has(from)) {
          if (position.castles.rook[position.turn].a === to) canonicalTo = kingCastlesTo(position.turn, 'a');
          else if (position.castles.rook[position.turn].h === to) canonicalTo = kingCastlesTo(position.turn, 'h');
        }
        for (const promotion of promotions) moves.push(makeUci({ from, to: canonicalTo, promotion }));
      }
    }
    const dropDestinations = position.dropDests(context);
    if (position.pockets) {
      for (const role of ROLES) {
        if (position.pockets[position.turn][role] <= 0) continue;
        const destinations = role === 'pawn' ? dropDestinations.diff(SquareSet.backranks()) : dropDestinations;
        for (const to of destinations) moves.push(makeUci({ role, to }));
      }
    }
    return [...new Set(moves)].sort();
  };

  const authorityTerminal = position => {
    if (position.isCheckmate()) {
      const winner = position.turn === 'white' ? 'black' : 'white';
      return { ended: true, reason: 'checkmate', winner, result: winner === 'white' ? '1-0' : '0-1' };
    }
    if (position.isStalemate()) return { ended: true, reason: 'stalemate', winner: null, result: '1/2-1/2' };
    return { ended: false, reason: 'ongoing', winner: null, result: '*' };
  };

  const nativeDiagnostics = position => {
    const outcome = position.outcome();
    return {
      is_insufficient_material: position.isInsufficientMaterial(),
      white_has_insufficient_material: position.hasInsufficientMaterial('white'),
      black_has_insufficient_material: position.hasInsufficientMaterial('black'),
      outcome_winner: outcome?.winner ?? null,
      repetition_supported: false,
      note: 'native insufficient-material outcome is diagnostic only and is not Lichess Crazyhouse authority',
    };
  };

  const describe = position => {
    const canonicalFen = canonicalizeFen(makeFen(position.toSetup()));
    return {
      ...physicalState(canonicalFen),
      in_check: position.isCheck(),
      legal_moves: legalMoves(position),
      terminal: authorityTerminal(position),
      native_diagnostics: nativeDiagnostics(position),
    };
  };

  const playMoves = (position, moves) => {
    moves.forEach((rawMove, index) => {
      if (typeof rawMove !== 'string') throw new AdapterError('INVALID_REQUEST', `moves[${index}] is not a string`);
      const move = parseUci(rawMove);
      if (!move) throw new AdapterError('INVALID_UCI', `moves[${index}] ${JSON.stringify(rawMove)} is not UCI`);
      if (!position.isLegal(move)) throw new AdapterError('ILLEGAL_MOVE', `moves[${index}] ${JSON.stringify(rawMove)} is illegal`);
      position.play(move);
    });
  };

  return { parsePosition, describe, playMoves, perft: debug.perft, canonicalizeFen, makeFen };
};

const requireRequest = request => {
  if (request === null || typeof request !== 'object' || Array.isArray(request)) throw new AdapterError('INVALID_REQUEST', 'request must be a JSON object');
  if (request.schema !== REQUEST_SCHEMA) throw new AdapterError('INVALID_SCHEMA', `expected ${REQUEST_SCHEMA}`);
  if (request.authority_profile !== AUTHORITY_PROFILE) throw new AdapterError('INVALID_PROFILE', `expected ${AUTHORITY_PROFILE}`);
  if (typeof request.id !== 'string' || request.id.length === 0) throw new AdapterError('INVALID_REQUEST', 'id must be a nonempty string');
  if (typeof request.op !== 'string') throw new AdapterError('INVALID_REQUEST', 'op must be a string');
  return [request.id, request.op];
};

const execute = (request, identity, operations) => {
  const [id, op] = requireRequest(request);
  const base = { schema: RESPONSE_SCHEMA, authority_profile: AUTHORITY_PROFILE, id, implementation: identity };
  if (op === 'capabilities') {
    return {
      ...base,
      ok: true,
      capabilities: {
        operations: ['capabilities', 'inspect', 'transition', 'perft'],
        fen_input: ['bracket_pocket', 'slash_pocket'],
        fen_output: 'bracket_pocket_PNBRQpnbrq_legal_ep',
        history: 'not_available',
        native_result_authority: false,
      },
    };
  }
  if (typeof request.fen !== 'string') throw new AdapterError('INVALID_REQUEST', 'fen must be a string');
  const position = operations.parsePosition(request.fen);
  if (op === 'inspect') return { ...base, ok: true, state: operations.describe(position) };
  if (op === 'transition') {
    if (!Array.isArray(request.moves)) throw new AdapterError('INVALID_REQUEST', 'transition moves must be an array');
    const rootFen = operations.canonicalizeFen(operations.makeFen(position.toSetup()));
    const working = position.clone();
    operations.playMoves(working, request.moves);
    const finalState = operations.describe(working);
    const rootUnchanged = operations.canonicalizeFen(operations.makeFen(position.toSetup())) === rootFen;
    if (!rootUnchanged) throw new AdapterError('ROOT_MUTATED', 'transition mutated the retained root position');
    return { ...base, ok: true, move_count: request.moves.length, root_unchanged: true, state: finalState };
  }
  if (op === 'perft') {
    if (!Number.isInteger(request.depth) || request.depth < 0 || request.depth > 6) throw new AdapterError('INVALID_REQUEST', 'depth must be an integer from 0 through 6');
    const before = operations.canonicalizeFen(operations.makeFen(position.toSetup()));
    const nodes = operations.perft(position, request.depth, false);
    const after = operations.canonicalizeFen(operations.makeFen(position.toSetup()));
    if (before !== after) throw new AdapterError('ROOT_MUTATED', 'perft mutated the root position');
    return { ...base, ok: true, depth: request.depth, nodes, root: operations.describe(position) };
  }
  throw new AdapterError('UNSUPPORTED_OPERATION', `unsupported operation ${JSON.stringify(op)}`);
};

const main = async () => {
  let args;
  let checkout;
  try {
    args = parseArgs(process.argv.slice(2));
    checkout = authenticateCheckout(args.requireRoot);
  } catch (error) {
    const code = error instanceof AdapterError ? error.code : 'STARTUP_FAILURE';
    process.stderr.write(`FATAL ${code}: ${error.message}\n`);
    return 2;
  }

  let operations;
  try {
    operations = createOperations(await loadModules(checkout.root));
  } catch (error) {
    process.stderr.write(`FATAL MODULE_LOAD_FAILURE: ${error.message}\n`);
    return 2;
  }

  const input = args.input === '-' ? fs.readFileSync(0, 'utf8') : fs.readFileSync(args.input, 'utf8');
  const responses = [];
  let failed = false;
  input.split(/\r?\n/).forEach((rawLine, index) => {
    const line = index === 0 ? rawLine.replace(/^\uFEFF/, '') : rawLine;
    if (!line.trim()) return;
    let request;
    let id = null;
    try {
      request = JSON.parse(line);
      if (request && typeof request.id === 'string') id = request.id;
      responses.push(execute(request, checkout.identity, operations));
    } catch (error) {
      failed = true;
      const adapterError = error instanceof AdapterError ? error : new AdapterError(error instanceof SyntaxError ? 'INVALID_JSON' : 'INTERNAL_ERROR', error.message);
      responses.push({
        schema: RESPONSE_SCHEMA,
        authority_profile: AUTHORITY_PROFILE,
        id,
        implementation: checkout.identity,
        ok: false,
        error: { code: adapterError.code, message: `line ${index + 1}: ${adapterError.message}` },
      });
    }
  });
  const output = responses.map(response => JSON.stringify(response)).join('\n') + (responses.length ? '\n' : '');
  if (args.output === '-') process.stdout.write(output);
  else fs.writeFileSync(args.output, output, { encoding: 'utf8', flag: 'wx' });
  return failed ? 1 : 0;
};

process.exitCode = await main();
