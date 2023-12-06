#!/usr/bin/env python3

import argparse
import datetime
import json
import pathlib
import subprocess
import sys
import traceback
from typing import Any, Literal, Union

import sgfmill.ascii_boards  # type: ignore[import]
import sgfmill.boards  # type: ignore[import]

Color = Union[Literal['b'], Literal['w']]
Move = Union[Literal['pass'], tuple[int, int]]
COLS = 'ABCDEFGHJKLMNOPQRSTUVWXYZ'

MAX_POINTS_LOST = 5.0
SETTLED_WEIGHT = 1.0
MIN_VISITS = 3
ATTACH_PENALTY = 1.0
TENUKI_PENALTY = 0.5
OPPONENT_FAC = 0.5

def main():
	katago = args_to_katago()
	try:
		GTPEngine(katago).run()
	finally:
		katago.close()

class KataGo:
	def __init__(self, katago_path: str, config_path: str, model_path: str, additional_args: list[str] = []):
		self.query_counter = 0
		self.katago = subprocess.Popen(
				[katago_path, 'analysis', '-config', config_path, '-model', model_path, *additional_args],
				stdin=subprocess.PIPE, stdout=subprocess.PIPE)

	def close(self):
		self.katago.stdin.close()

	def query(self, size: int, moves: list[tuple[Color, str]], handicap_stones: list[tuple[Color, str]],
			komi: float, max_visits: int | None=None):
		query = {
			'id': str(self.query_counter),
			'moves': moves,
			'initialStones': handicap_stones,
			'rules': 'Chinese',
			'komi': komi,
			'boardXSize': size,
			'boardYSize': size,
			'includeMovesOwnership': True,
		}
		self.query_counter += 1

		if max_visits is not None:
			query['maxVisits'] = max_visits
		return self.query_raw(query)

	def query_raw(self, query: dict[str, Any]):
		assert self.katago.stdin and self.katago.stdout
		self.katago.stdin.write((json.dumps(query) + '\n').encode())
		self.katago.stdin.flush()

		while True:
			if self.katago.poll() is not None:
				raise Exception('Unexpected katago exit')
			line = self.katago.stdout.readline()
			if line != '':
				return json.loads(line.decode())

def args_to_katago() -> KataGo:
	description = """run KataGo analysis engine as a GTP bot"""
	parser = argparse.ArgumentParser(description=description)
	parser.add_argument('--katago-path', help='Path to katago executable', required=True)
	parser.add_argument('--config-path',
			help='Path to KataGo analysis config (e.g. cpp/configs/analysis_example.cfg in KataGo repo)',
			required=True)
	parser.add_argument('--model-path', help='Path to neural network .bin.gz file', required=True)
	args = vars(parser.parse_args())

	return KataGo(args['katago_path'], args['config_path'], args['model_path'])

class GTPEngine:
	def __init__(self, katago: KataGo) -> None:
		self.katago = katago
		self.commands = {
			'list_commands': self.list_commands,
			'boardsize': self.boardsize,
			'komi': self.set_komi,
			'set_free_handicap': self.set_free_handicap,
			'place_free_handicap': self.place_free_handicap,
			'play': self.play,
			'genmove': self.genmove,
		}
		self.size = 19
		self.komi = 7.5
		self.board = sgfmill.boards.Board(self.size)
		self.handicap_stones: list[tuple[Color, str]] = []
		self.moves: list[tuple[Color, str]] = []
		self.next_player: Color = 'b'
		self.score_lead = None
		self.consecutive_passes = 0
		log_path = pathlib.Path('logs', datetime.datetime.now().isoformat())
		log_path.parent.mkdir(exist_ok=True)
		self.log_file = log_path.open('x', buffering=1)

	def run(self) -> None:
		while True:
			try:
				split = input().rstrip().split(' ', 1)
			except EOFError:
				break
			command = split[0]
			if command == 'quit':
				break
			handler = self.commands.get(command)
			if handler is None:
				response = ''
			else:
				args = ''
				if len(split) == 2:
					args = split[1]
				try:
					response = handler(args)
				except Exception:
					self.log(traceback.format_exc())
					raise
			print(f'= {response}\n')
			sys.stdout.flush()
		self.log_file.close()

	def list_commands(self, args: str) -> str:
		return '\n'.join(self.commands.keys())

	def boardsize(self, args: str) -> str:
		self.size = int(args) # sgfmill doesn't support non-square boards
		assert self.size <= len(COLS)
		self.board = sgfmill.boards.Board(self.size)
		return ''

	def set_komi(self, args: str) -> str:
		self.komi = float(args)
		return ''

	def set_free_handicap(self, args: str) -> str:
		"""opponent sets handicap stones"""
		for stone in args.split():
			stone = stone.upper()
			coords = str_to_sgfmill(stone)
			self.handicap_stones.append(('b', stone))
			self.board.play(*coords, 'b')
		self.next_player = 'w'
		return ''

	def place_free_handicap(self, args: str) -> str:
		"""bot sets handicap stones"""
		num_stones = int(args)
		stones = ['D4', 'Q16', 'D16', 'Q4', 'D10', 'Q10', 'K4', 'K16', 'K10'][:num_stones]
		for stone in stones:
			coords = str_to_sgfmill(stone)
			self.handicap_stones.append(('b', stone))
			self.board.play(*coords, 'b')
		self.next_player = 'w'
		return ' '.join(stones)

	def play(self, args: str) -> str:
		player, move = args.split()
		assert player in ('black', 'white')
		if move == 'pass':
			self.moves.append((player[0], 'pass')) # type: ignore[arg-type]
			self.consecutive_passes += 1
		else:
			move = move.upper()
			self.moves.append((player[0], move)) # type: ignore[arg-type]
			coords = str_to_sgfmill(move)
			self.board.play(*coords, args[0])
			self.consecutive_passes = 0
		self.log('opponent played', move)

		if args[0] == 'b':
			self.next_player = 'w'
		else:
			self.next_player = 'b'
		return ''

	def genmove(self, args: str) -> str:
		assert args[0] == self.next_player
		if self.next_player == 'b':
			opponent: Color = 'w'
		else:
			opponent = 'b'

		if self.consecutive_passes >= 3 and len(self.moves) > 2 * self.size:
			print(f'DISCUSSION:since you passed 3 times after move {2 * self.size}, I will pass as well',
				file=sys.stderr)
			ai_move = 'pass'
		else:
			ai_move = self.query_ai_move(opponent)

		if ai_move == 'pass':
			self.moves.append((self.next_player, 'pass'))
		else:
			self.moves.append((self.next_player, ai_move))
			ai_move_coords = str_to_sgfmill(ai_move)
			self.board.play(*ai_move_coords, self.next_player)
		# print(sgfmill.ascii_boards.render_board(self.board), file=sys.stderr)
		self.next_player = opponent
		self.log('playing', ai_move)
		return ai_move

	def query_ai_move(self, opponent: Color) -> str:
		sign = {'b': 1, 'w': -1}[self.next_player]
		analysis = self.katago.query(self.size, self.moves, self.handicap_stones, self.komi, max_visits=100)
		assert analysis['rootInfo']['currentPlayer'] == self.next_player.upper()
		candidate_ai_moves = candidate_moves(analysis, sign)

		if self.score_lead is not None:
			current_lead = analysis['rootInfo']['scoreLead']
			score_delta = abs(current_lead - self.score_lead)
			if score_delta > 2.0:
				last_move = self.moves[-1][1]
				print(f'MALKOVICH:{last_move} caused a significant score change: {score_delta:.1f} points.',
						f'score lead: {current_lead:.1f}', file=sys.stderr)
		self.score_lead = analysis['rootInfo']['scoreLead']

		def settledness(d: dict, player_sign: int) -> float:
			return sum([abs(o) for o in d['ownership'] if player_sign * o > 0])

		def board_pos(x: int, y: int) -> Color | None:
			try:
				return self.board.get(x, y)
			except IndexError:
				return None

		def is_attachment(move: str) -> bool:
			if move == 'pass':
				return False
			x, y = str_to_sgfmill(move)
			attach_opponent_stones = sum(
				board_pos(x + dx, y + dy) == opponent
				for dx in [-1, 0, 1]
				for dy in [-1, 0, 1]
				if abs(dx) + abs(dy) == 1
			)
			nearby_own_stones = sum(
				board_pos(x + dx, y + dy) == self.next_player
				for dx in [-2, 0, 1, 2]
				for dy in [-2 - 1, 0, 1, 2]
				if abs(dx) + abs(dy) <= 2  # allows clamps/jumps
			)
			return attach_opponent_stones >= 1 and nearby_own_stones == 0

		def is_tenuki(move: str) -> bool:
			if move == 'pass' or len(self.moves) < 2:
				return False
			move_coords = str_to_sgfmill(move)
			for prev in self.moves[-2:]:
				_, prev_move = prev
				if prev_move == 'pass':
					return False
				prev_move_coords = str_to_sgfmill(prev_move)
				if max(abs(last_c - cand_c) for last_c, cand_c in zip(prev_move_coords, move_coords)) < 5:
					return False
			return True

		moves_with_settledness = sorted(
			[
				(
					move,
					settledness(d, sign),
					settledness(d, -sign),
					is_attachment(move),
					is_tenuki(move),
					d,
				)
				for d in candidate_ai_moves
				if d['pointsLost'] < MAX_POINTS_LOST
				and 'ownership' in d
				and (d['order'] <= 1 or d['visits'] >= MIN_VISITS)
				for move in [d['move']]
				if not (move == 'pass' and d['pointsLost'] > 0.75)
			],
			key=lambda t: t[5]['pointsLost']
			+ ATTACH_PENALTY * t[3]
			+ TENUKI_PENALTY * t[4]
			- SETTLED_WEIGHT * (t[1] + OPPONENT_FAC * t[2]),
		)
		if not moves_with_settledness:
			raise Exception('No moves found - are you using an older KataGo with no per-move ownership info?')
		ai_move = moves_with_settledness[0][0]

		cands = [
			f"{move} ({d['pointsLost']:.1f} pt lost, {d['visits']} visits, {settled:.1f} settledness, "
			f"{oppsettled:.1f} opponent settledness{', attachment' if isattach else ''}"
			f"{', tenuki' if istenuki else ''})"
			for move, settled, oppsettled, isattach, istenuki, d in moves_with_settledness[:5]
		]
		ai_thoughts = f"top 5 candidates {', '.join(cands)} "
		self.log(ai_thoughts)
		return ai_move

	def log(self, *msg: str) -> None:
		self.log_file.write(f'[{len(self.moves)}] {" ".join(msg)}\n')

def sgfmill_to_str(move: Move) -> str:
	if move == 'pass':
		return 'pass'
	(y, x) = move
	return COLS[x] + str(y + 1)

def str_to_sgfmill(s: str) -> tuple[int, int]:
	return int(s[1:]) - 1, COLS.index(s[0])

def candidate_moves(analysis: dict, sign: int) -> list[dict]:
	# https://github.com/sanderland/katrain/blob/98ce47c3bc5f1c8c1a9cc03120e4afcd2cf677db/katrain/core/game_node.py#L412
	root_score = analysis['rootInfo']['scoreLead']
	root_winrate = analysis['rootInfo']['winrate']
	move_dicts = analysis['moveInfos']
	top_move = [d for d in move_dicts if d['order'] == 0]
	top_score_lead = top_move[0]['scoreLead'] if top_move else root_score
	return sorted(
		[
			{
				'pointsLost': sign * (root_score - d['scoreLead']),
				'relativePointsLost': sign * (top_score_lead - d['scoreLead']),
				'winrateLost': sign * (root_winrate - d['winrate']),
				**d,
			}
			for d in move_dicts
		],
		key=lambda d: (d['order'], d['pointsLost']),
	)

if __name__ == '__main__':
	main()
