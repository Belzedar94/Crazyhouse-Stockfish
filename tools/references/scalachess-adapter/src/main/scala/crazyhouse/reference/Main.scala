package crazyhouse.reference

import com.fasterxml.jackson.core.JsonProcessingException
import chess.*
import chess.CanPlay.*
import chess.format.Fen
import chess.variant.Crazyhouse
import play.api.libs.json.*

import java.io.{ BufferedWriter, OutputStreamWriter }
import java.nio.charset.StandardCharsets
import java.nio.file.{ Files, Path, Paths, StandardOpenOption }
import scala.io.Source
import scala.util.control.NonFatal

object Main:

  private val RequestSchema = "crazyhouse-reference-request/v1"
  private val ResponseSchema = "crazyhouse-reference-response/v1"
  private val AuthorityProfile = "LICHESS_CRAZYHOUSE_2026_08_12"
  private val ExpectedCommit = "cbffc9d7e2c6f8ba33381c5403e1b4f992199626"
  private val ExpectedTree = "f5410eb2a6ddb6ef7092317533f704158c86a4fc"
  private val PocketOrder = "PNBRQpnbrq"

  private final class AdapterException(val code: String, message: String) extends RuntimeException(message)

  private final case class Arguments(input: String = "-", output: String = "-")

  private def fail(code: String, message: String): Nothing = throw AdapterException(code, message)

  private def parseArgs(args: Array[String]): Arguments =
    var parsed = Arguments()
    var index = 0
    while index < args.length do
      if index + 1 >= args.length then fail("INVALID_ARGUMENTS", s"missing value for ${args(index)}")
      args(index) match
        case "--input"  => parsed = parsed.copy(input = args(index + 1))
        case "--output" => parsed = parsed.copy(output = args(index + 1))
        case flag       => fail("INVALID_ARGUMENTS", s"unknown argument $flag")
      index += 2
    parsed

  private def git(root: Path, args: String*): String =
    val command = Seq("git", "-C", root.toString) ++ args
    val process = ProcessBuilder(command*).redirectErrorStream(true).start()
    val output = Source.fromInputStream(process.getInputStream, "UTF-8").mkString.trim
    val exitCode = process.waitFor()
    if exitCode != 0 then fail("IDENTITY_UNAVAILABLE", s"git identity probe failed: $output")
    output

  private def authenticateCheckout(): JsObject =
    val rootValue = Option(System.getProperty("scalachess.identity.root"))
      .filter(_.nonEmpty)
      .getOrElse(fail("IDENTITY_UNAVAILABLE", "scalachess.identity.root system property is required"))
    val root = Paths.get(rootValue).toRealPath()
    val commit = git(root, "rev-parse", "HEAD")
    val tree = git(root, "rev-parse", "HEAD^{tree}")
    val dirty = git(root, "status", "--porcelain")
    if commit != ExpectedCommit || tree != ExpectedTree then
      fail("WRONG_REFERENCE_IDENTITY", s"expected $ExpectedCommit/$ExpectedTree, got $commit/$tree")
    if dirty.nonEmpty then fail("DIRTY_REFERENCE", "scalachess checkout is not clean")

    Json.obj(
      "name" -> "scalachess",
      "version" -> "17.16.1",
      "commit" -> commit,
      "tree" -> tree,
      "root" -> root.toString,
      "license" -> "MIT",
      "role" -> "primary_rules_and_result_reference",
      "result_authority" -> true
    )

  private def canonicalizeFen(fen: String): String =
    val fields = fen.trim.split("\\s+").toList
    if fields.size != 6 then fail("INVALID_FEN", s"expected six FEN fields, got ${fields.size}")
    val boardAndPocket = fields.head
    val (board, pocket) =
      if boardAndPocket.endsWith("]") && boardAndPocket.contains("[") then
        val splitAt = boardAndPocket.lastIndexOf('[')
        boardAndPocket.take(splitAt) -> boardAndPocket.slice(splitAt + 1, boardAndPocket.length - 1)
      else if boardAndPocket.count(_ == '/') == 8 then
        val ranks = boardAndPocket.split("/", -1)
        ranks.take(8).mkString("/") -> ranks(8)
      else if boardAndPocket.count(_ == '/') == 7 then boardAndPocket -> ""
      else fail("INVALID_FEN", "Crazyhouse board field has neither eight ranks nor a pocket field")
    if board.count(_ == '/') != 7 then fail("INVALID_FEN", "Crazyhouse board field does not contain eight ranks")
    if pocket.exists(symbol => !PocketOrder.contains(symbol)) then
      fail("INVALID_FEN", "pocket contains an unsupported piece symbol")
    val orderedPocket = PocketOrder.flatMap(symbol => symbol.toString * pocket.count(_ == symbol))
    (s"$board[$orderedPocket]" :: fields.tail).mkString(" ")

  private def physicalState(canonicalFen: String): JsObject =
    val fields = canonicalFen.split(" ")
    val boardAndPocket = fields(0)
    val splitAt = boardAndPocket.lastIndexOf('[')
    val board = boardAndPocket.take(splitAt)
    val pocket = boardAndPocket.slice(splitAt + 1, boardAndPocket.length - 1)
    val promoted = List.newBuilder[String]
    board.split("/").zipWithIndex.foreach: (rank, rankIndex) =>
      var fileIndex = 0
      var previousSquare: Option[String] = None
      rank.foreach:
        case digit if digit.isDigit =>
          fileIndex += digit.asDigit
          previousSquare = None
        case '~' =>
          previousSquare.fold(fail("INVALID_FEN", "promoted marker is not attached to a board piece"))(promoted += _)
        case _ =>
          if fileIndex >= 8 then fail("INVALID_FEN", "rank exceeds eight files")
          previousSquare = Some(s"${('a' + fileIndex).toChar}${8 - rankIndex}")
          fileIndex += 1
      if fileIndex != 8 then fail("INVALID_FEN", "rank does not contain eight files")

    def pocketJson(uppercase: Boolean): JsObject =
      val roles = List("pawn" -> 'P', "knight" -> 'N', "bishop" -> 'B', "rook" -> 'R', "queen" -> 'Q')
      JsObject(roles.map: (role, symbol) =>
        val actual = if uppercase then symbol else symbol.toLower
        role -> JsNumber(pocket.count(_ == actual))
      )

    Json.obj(
      "canonical_fen" -> canonicalFen,
      "turn" -> (if fields(1) == "w" then "white" else "black"),
      "castling_rights" -> fields(2),
      "ep_square" -> (if fields(3) == "-" then JsNull else JsString(fields(3))),
      "halfmove_clock" -> fields(4).toInt,
      "fullmove_number" -> fields(5).toInt,
      "pockets" -> Json.obj("white" -> pocketJson(true), "black" -> pocketJson(false)),
      "promoted_squares" -> promoted.result().sorted
    )

  private def parsePosition(fen: String): Position.AndFullMoveNumber =
    val inputFields = canonicalizeFen(fen).split(" ")
    val halfmove = inputFields(4).toIntOption.getOrElse(fail("INVALID_FEN", "halfmove clock is not an integer"))
    val fullmove = inputFields(5).toIntOption.getOrElse(fail("INVALID_FEN", "fullmove number is not an integer"))
    if halfmove < 0 || halfmove > 100 then fail("LOSSY_COUNTER_RANGE", "scalachess accepts halfmove clocks only from 0 through 100")
    if fullmove < 1 || fullmove > 500 then fail("LOSSY_COUNTER_RANGE", "scalachess accepts fullmove numbers only from 1 through 500")
    val parsed = Fen
      .readWithMoveNumber(Crazyhouse, Fen.Full.clean(fen))
      .getOrElse(fail("INVALID_FEN", "scalachess rejected the FEN"))
    if !Crazyhouse.valid(parsed.position, strict = true) then fail("INVALID_POSITION", "scalachess strict Crazyhouse validation failed")
    if parsed.position.withColor(!parsed.position.color).check.yes then
      fail("INVALID_POSITION", "the side that just moved remains in check")
    parsed

  private def legalMoves(position: Position): List[String] =
    Crazyhouse
      .legalMoves(position)
      .map:
        case move: Move => move.castle.fold(move.toUci.uci)(castle => s"${move.orig.key}${castle.kingTo.key}")
        case drop: Drop => drop.toUci.uci
      .distinct
      .sorted

  private def authorityTerminal(position: Position): JsObject =
    if position.checkMate then
      val winner = (!position.color).name
      Json.obj("ended" -> true, "reason" -> "checkmate", "winner" -> winner, "result" -> (if winner == "white" then "1-0" else "0-1"))
    else if position.variantEnd then
      val winner = position.winner.map(_.name)
      val result = winner.fold("1/2-1/2")(side => if side == "white" then "1-0" else "0-1")
      Json.obj("ended" -> true, "reason" -> "variant_end", "winner" -> winner, "result" -> result)
    else if position.staleMate then
      Json.obj("ended" -> true, "reason" -> "stalemate", "winner" -> JsNull, "result" -> "1/2-1/2")
    else if position.history.fivefoldRepetition then
      Json.obj("ended" -> true, "reason" -> "fivefold_repetition", "winner" -> JsNull, "result" -> "1/2-1/2")
    else Json.obj("ended" -> false, "reason" -> "ongoing", "winner" -> JsNull, "result" -> "*")

  private def nativeDiagnostics(position: Position): JsObject =
    Json.obj(
      "is_insufficient_material" -> Crazyhouse.isInsufficientMaterial(position),
      "opponent_has_insufficient_material" -> Crazyhouse.opponentHasInsufficientMaterial(position),
      "fifty_moves" -> Crazyhouse.fiftyMoves(position.history),
      "fivefold_repetition" -> position.history.fivefoldRepetition,
      "threefold_repetition" -> position.threefoldRepetition,
      "auto_draw" -> position.autoDraw
    )

  private def describe(parsed: Position.AndFullMoveNumber): JsObject =
    val nativeFen = Fen.write(parsed).value
    val canonicalFen = canonicalizeFen(nativeFen)
    physicalState(canonicalFen) ++ Json.obj(
      "native_fen" -> nativeFen,
      "in_check" -> parsed.position.check.yes,
      "legal_moves" -> legalMoves(parsed.position),
      "terminal" -> authorityTerminal(parsed.position),
      "native_diagnostics" -> nativeDiagnostics(parsed.position)
    )

  private def playMoves(
      start: Position.AndFullMoveNumber,
      moves: Seq[String]
  ): Position.AndFullMoveNumber =
    moves.zipWithIndex.foldLeft(start) { case (current, (rawMove, index)) =>
        current.playUci(rawMove) match
          case Left(error) => fail("ILLEGAL_MOVE", s"moves[$index] '$rawMove': ${error.value}")
          case Right((next, move)) =>
            if move.before != current.position then
              fail("PREDECESSOR_MISMATCH", s"moves[$index] predecessor does not equal current position")
            next
    }

  private def perft(position: Position, depth: Int): Long =
    if depth == 0 then 1L
    else Crazyhouse.legalMoves(position).foldLeft(0L): (nodes, move) =>
      Math.addExact(nodes, perft(move.after, depth - 1))

  private def requiredString(request: JsObject, field: String): String =
    (request \ field).asOpt[String].filter(_.nonEmpty).getOrElse(fail("INVALID_REQUEST", s"$field must be a nonempty string"))

  private def execute(requestValue: JsValue, identity: JsObject): JsObject =
    val request = requestValue.asOpt[JsObject].getOrElse(fail("INVALID_REQUEST", "request must be a JSON object"))
    if requiredString(request, "schema") != RequestSchema then fail("INVALID_SCHEMA", s"expected $RequestSchema")
    if requiredString(request, "authority_profile") != AuthorityProfile then fail("INVALID_PROFILE", s"expected $AuthorityProfile")
    val id = requiredString(request, "id")
    val op = requiredString(request, "op")
    val base = Json.obj(
      "schema" -> ResponseSchema,
      "authority_profile" -> AuthorityProfile,
      "id" -> id,
      "implementation" -> identity
    )

    op match
      case "capabilities" =>
        base ++ Json.obj(
          "ok" -> true,
          "capabilities" -> Json.obj(
            "operations" -> Seq("capabilities", "inspect", "transition", "perft"),
            "fen_input" -> Seq("bracket_pocket", "slash_pocket"),
            "fen_output" -> "bracket_pocket_PNBRQpnbrq_legal_ep",
            "history" -> "move_sequence_with_position_hashes",
            "counter_limits" -> Json.obj("halfmove_max" -> 100, "fullmove_max" -> 500),
            "native_result_authority" -> true
          )
        )
      case "inspect" =>
        val parsed = parsePosition(requiredString(request, "fen"))
        base ++ Json.obj("ok" -> true, "state" -> describe(parsed))
      case "transition" =>
        val parsed = parsePosition(requiredString(request, "fen"))
        val moves = (request \ "moves").validate[Seq[String]].fold(
          _ => fail("INVALID_REQUEST", "transition moves must be an array of strings"),
          values => values
        )
        val rootFen = canonicalizeFen(Fen.write(parsed).value)
        val finalPosition = playMoves(parsed, moves)
        if canonicalizeFen(Fen.write(parsed).value) != rootFen then fail("ROOT_MUTATED", "transition mutated the retained root position")
        base ++ Json.obj(
          "ok" -> true,
          "move_count" -> moves.size,
          "root_unchanged" -> true,
          "predecessors_verified" -> true,
          "state" -> describe(finalPosition)
        )
      case "perft" =>
        val parsed = parsePosition(requiredString(request, "fen"))
        val depth = (request \ "depth").asOpt[Int].filter(value => value >= 0 && value <= 6)
          .getOrElse(fail("INVALID_REQUEST", "depth must be an integer from 0 through 6"))
        val rootFen = canonicalizeFen(Fen.write(parsed).value)
        val nodes = perft(parsed.position, depth)
        if canonicalizeFen(Fen.write(parsed).value) != rootFen then fail("ROOT_MUTATED", "perft mutated the root position")
        base ++ Json.obj("ok" -> true, "depth" -> depth, "nodes" -> nodes, "root" -> describe(parsed))
      case other => fail("UNSUPPORTED_OPERATION", s"unsupported operation '$other'")

  private def responseError(
      id: Option[String],
      identity: JsObject,
      code: String,
      message: String
  ): JsObject =
    Json.obj(
      "schema" -> ResponseSchema,
      "authority_profile" -> AuthorityProfile,
      "id" -> id.fold[JsValue](JsNull)(JsString.apply),
      "implementation" -> identity,
      "ok" -> false,
      "error" -> Json.obj("code" -> code, "message" -> message)
    )

  private def run(args: Array[String]): Int =
    val parsedArgs = parseArgs(args)
    val identity = authenticateCheckout()
    val source = if parsedArgs.input == "-" then Source.stdin else Source.fromFile(parsedArgs.input, "UTF-8")
    val writer =
      if parsedArgs.output == "-" then BufferedWriter(OutputStreamWriter(System.out, StandardCharsets.UTF_8))
      else
        Files.newBufferedWriter(
          Paths.get(parsedArgs.output),
          StandardCharsets.UTF_8,
          StandardOpenOption.CREATE_NEW,
          StandardOpenOption.WRITE
        )
    var failed = false
    try
      source.getLines().zipWithIndex.foreach: (rawLine, index) =>
        val line = if index == 0 then rawLine.stripPrefix("\ufeff") else rawLine
        if line.trim.nonEmpty then
          var id: Option[String] = None
          val response =
            try
              val request = Json.parse(line)
              id = (request \ "id").asOpt[String]
              execute(request, identity)
            catch
              case error: AdapterException =>
                failed = true
                responseError(id, identity, error.code, s"line ${index + 1}: ${error.getMessage}")
              case error: JsonProcessingException =>
                failed = true
                responseError(id, identity, "INVALID_JSON", s"line ${index + 1}: ${error.getOriginalMessage}")
              case error: JsResultException =>
                failed = true
                responseError(id, identity, "INVALID_JSON", s"line ${index + 1}: ${error.getMessage}")
              case NonFatal(error) =>
                failed = true
                responseError(id, identity, "INTERNAL_ERROR", s"line ${index + 1}: ${error.getMessage}")
          writer.write(Json.stringify(response))
          writer.newLine()
          writer.flush()
    finally
      source.close()
      writer.close()
    if failed then 1 else 0

  def main(args: Array[String]): Unit =
    val exitCode =
      try run(args)
      catch
        case error: AdapterException =>
          System.err.println(s"FATAL ${error.code}: ${error.getMessage}")
          2
        case NonFatal(error) =>
          System.err.println(s"FATAL STARTUP_FAILURE: ${error.getMessage}")
          2
    if exitCode != 0 then System.exit(exitCode)
