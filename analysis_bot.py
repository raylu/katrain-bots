#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from typing import Any, Literal, Union

import sgfmill.ascii_boards
import sgfmill.boards

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
		katago = subprocess.Popen(
				[katago_path, 'analysis', '-config', config_path, '-model', model_path, *additional_args],
				stdin=subprocess.PIPE, stdout=subprocess.PIPE)
		self.katago = katago

	def close(self):
		self.katago.stdin.close()

	def query(self, size: int, moves: list[tuple[Color, Move]], komi: float, max_visits: int | None=None):
		query = {
			'id': str(self.query_counter),
			'moves': [(color, sgfmill_to_str(move)) for color, move in moves],
			'initialStones': [],
			'rules': 'Chinese',
			'komi': komi,
			'boardXSize': size,
			'boardYSize': size,
			'includeMovesOwnership': True,
		}
		self.query_counter += 1

		# for y in range(initial_board.side):
		# 	for x in range(initial_board.side):
		# 		color = initial_board.get(y, x)
		# 		if color:
		# 			query['initialStones'].append((color, sgfmill_to_str((y, x))))
		if max_visits is not None:
			query['maxVisits'] = max_visits
		return self.query_raw(query)

	def query_raw(self, query: dict[str, Any]):
		self.katago.stdin.write((json.dumps(query) + '\n').encode())
		self.katago.stdin.flush()

		while True:
			if self.katago.poll() is not None:
				raise Exception('Unexpected katago exit')
			line = self.katago.stdout.readline()
			if line != '':
				return json.loads(line.decode())

def args_to_katago() -> KataGo:
	description = """
	Example script showing how to run KataGo analysis engine and query it from python.
	"""
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
			'play': self.play,
			'genmove': self.genmove,
			# TODO: place_free_handicap, set_free_handicap
		}
		self.size = 19
		self.komi = 7.5
		self.board = sgfmill.boards.Board(self.size)
		self.moves: list[tuple[Color, Move]] = []
		self.next_player: Color = 'b'
		self.score_lead = None

	def run(self) -> None:
		while True:
			try:
				split = input().rstrip().split(' ', 1)
			except EOFError:
				break
			command = split[0]
			handler = self.commands.get(command)
			if handler is None:
				response = ''
			else:
				args = ''
				if len(split) == 2:
					args = split[1]
				response = handler(args)
			print(f'= {response}\n')
			sys.stdout.flush()
		return

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

	def play(self, args: str) -> str:
		player, move = args.split()
		assert player in ('black', 'white')
		if move == 'pass':
			self.moves.append((args[0], 'pass'))
		else:
			coords = str_to_sgfmill(move.upper())
			self.moves.append((args[0], coords))
			self.board.play(coords[0], coords[1], args[0])

		if args[0] == 'b':
			self.next_player = 'w'
		else:
			self.next_player = 'b'
		return ''

	def genmove(self, args: str) -> str:
		assert args[0] == self.next_player
		sign = {'b': 1, 'w': -1}[self.next_player]

		analysis = self.katago.query(self.size, self.moves, self.komi, max_visits=100)
		assert analysis['rootInfo']['currentPlayer'] == self.next_player.upper()
		candidate_ai_moves = candidate_moves(analysis, sign)

		if self.score_lead is not None:
			current_lead = analysis['rootInfo']['scoreLead']
			score_delta = abs(current_lead - self.score_lead)
			if score_delta > 2.0:
				last_move = sgfmill_to_str(self.moves[-1][1])
				print(f'MALKOVICH:{last_move} caused a significant score change: {score_delta:.1f} points.',
						f'score lead: {current_lead:.1f}', file=sys.stderr)
		self.score_lead = analysis['rootInfo']['scoreLead']
		
		if self.next_player == 'b':
			opponent = 'w'
		else:
			opponent = 'b'

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
				if max(abs(last_c - cand_c) for last_c, cand_c in zip(prev_move, move_coords)) < 5:
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
				if d["pointsLost"] < MAX_POINTS_LOST
				and "ownership" in d
				and (d["order"] <= 1 or d["visits"] >= MIN_VISITS)
				for move in [d["move"]]
				if not (move == 'pass' and d["pointsLost"] > 0.75)
			],
			key=lambda t: t[5]["pointsLost"]
			+ ATTACH_PENALTY * t[3]
			+ TENUKI_PENALTY * t[4]
			- SETTLED_WEIGHT * (t[1] + OPPONENT_FAC * t[2]),
		)
		if not moves_with_settledness:
			raise Exception("No moves found - are you using an older KataGo with no per-move ownership info?")
		ai_move = moves_with_settledness[0][0]

		cands = [
			f"{move} ({d['pointsLost']:.1f} pt lost, {d['visits']} visits, {settled:.1f} settledness, {oppsettled:.1f} opponent settledness{', attachment' if isattach else ''}{', tenuki' if istenuki else ''})"
			for move, settled, oppsettled, isattach, istenuki, d in moves_with_settledness[:5]
		]
		ai_thoughts = f"top 5 candidates {', '.join(cands)} "
		print(ai_thoughts, file=sys.stderr)

		if ai_move == 'pass':
			self.moves.append((args[0], 'pass'))
		else:
			ai_move_coords = str_to_sgfmill(ai_move)
			self.moves.append((self.next_player, ai_move_coords))
			self.board.play(ai_move_coords[0], ai_move_coords[1], self.next_player)
		# print(sgfmill.ascii_boards.render_board(self.board), file=sys.stderr)
		self.next_player = opponent
		return ai_move

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
