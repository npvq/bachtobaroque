from itertools import permutations
from copy import deepcopy
# from fractions import Fraction
# from enum import IntEnum

from music21.note import Note
from music21.pitch import Pitch
from music21.chord import Chord
from music21.roman import RomanNumeral
from music21.key import Key
from music21.interval import Interval, Specifier

from music21.meter import TimeSignature
from music21.clef import BassClef, TrebleClef
from music21.instrument import Piano
from music21.stream import Part, Score, Voice

from settings import default_config


DEBUG = True
import time
def dbg(msg):
	if DEBUG:
		print("DBG:", msg)


# Executive decision: calculations will use midi values. Pitch class = midi value % 12.
# Actually nvm: this cannot be done because we need to differentiate accidentals like double sharps for example

# alternative to pitchClass, returns 0..6 for C..B (for comparison purposes)
def scale_value(note_name):
	return ['C', 'D', 'E', 'F', 'G', 'A', 'B'].index(note_name[0])

# helper to fetch resolution target notes (ignore return value) [also note strict inequality >/<]
aboveOctave = lambda t_p, p: p.octave if scale_value(t_p.name) > scale_value(p.name) else p.octave+1 # (target pitch and reference pitch)
belowOctave = lambda t_p, p: p.octave if scale_value(t_p.name) < scale_value(p.name) else p.octave-1

# class Solfege(IntEnum):
# 	DO = 0
# 	RE = 1
# 	MI = 2
# 	FA = 3
# 	SOL = 4
# 	LA = 5
# 	TI = 6


class SATB(object):

	def __init__(self, **kwargs):
		self.config = default_config
		self.config.update(kwargs)

		self.ranges = [[self.config['bass_range_min'], self.config['bass_range_max'], self.config['bass_range_min_allowable'], self.config['bass_range_max_allowable']],
					   [self.config['tenor_range_min'], self.config['tenor_range_max'], self.config['tenor_range_min_allowable'], self.config['tenor_range_max_allowable']],
					   [self.config['alto_range_min'], self.config['alto_range_max'], self.config['alto_range_min_allowable'], self.config['alto_range_max_allowable']],
					   [self.config['soprano_range_min'], self.config['soprano_range_max'], self.config['soprano_range_min_allowable'], self.config['soprano_range_max_allowable']]]

		self.midi_ranges = [[p.midi for p in l] for l in self.ranges]

		# DP Memory Storage
		self.confidence = 1.2
		self.buffer = 5
		self.first_chord_buffer = 10000

	@staticmethod
	def _generate_pitches(query, lb=Pitch('A0'), ub=Pitch('C8')): # lower & upper bounds
		"""Query all notes in range of a certain pitch-class. Those outside the common range will be assigned a cost."""
		
		# We start checking from the same octave as the lower bound
		query.octave = lb.octave
		
		while (query.midi <= ub.midi):
			if (query.midi >= lb.midi):
				yield query
			query.octave += 1

	def _generate_voicings(self, chordMembers):
		"""Given four notes to voice, try to voice using self.voices
		   Also tasked to handle voicing rules. dist SA, AT < 8ve, dist TB < 2-8ves"""
		
		assert len(chordMembers) == 4

		# fetches appropriate octave value one voice down. (prevents overlapping and spacing errors)
		# note: same note twice means go an octave down, hence the strict inequality (<)
		nextOctaveDown = lambda new_name, last_pitch: last_pitch.octave if scale_value(new_name) < scale_value(last_pitch.name) else last_pitch.octave-1

		# Strategy: try a random Soprano note and use spacing rules to go down,
		# for Alto and Tenor there is basically only one correct choice; we are locked in.
		bass = chordMembers.pop(0)
		# sn,an,tn,bass: note names of chord members. s,a,t,b: actual pitches
		for sn, an, tn in permutations(chordMembers, 3):
			for s in self._generate_pitches(Pitch(sn), lb=self.ranges[3][2], ub=self.ranges[3][3]):
				a = Pitch(an)
				a.octave = nextOctaveDown(an, s)
				t = Pitch(tn)
				t.octave = nextOctaveDown(tn, a)
				if not (self.ranges[2][2]<=a<=self.ranges[2][3] and self.ranges[1][2]<=t<=self.ranges[1][3]):
					continue # make sure Alto and Tenor are in range
				for b in self._generate_pitches(Pitch(bass), lb=max(self.ranges[0][2], t.transpose('-d15')), ub=min(self.ranges[0][3], t.transpose('-m2'))):
					yield Chord(deepcopy([b,t,a,s]))

	def voiceChord(self, rm): # roman numeral
		"""Generates possible 4-part voicings for a triad or seventh chord.
		   Secondary dominants should be given in the key they tonicize, rather than the home key of the progression"""

		# This step mostly determines doubling and avoids doubling leading/tendency tones.
		# Seventh chords can have incompelete voicings, but the fa->mi of seventh chords cannot be doubled (PSR Rule)

		# LT for secondary dominants are adjusted to the LT in the secondary key.
		if rm.secondaryRomanNumeral:
			LT = rm.secondaryRomanNumeralKey.getLeadingTone().name
		else:
			LT = rm.key.getLeadingTone().name # Leading Tone

		# print(LT) # debug
		
		# WARNING: make sure the first element in the list is the root

		if rm.containsSeventh():
			# SEVENTH CHORD
			yield from self._generate_voicings([pitch.name for pitch in rm.pitches])
			# Incomplete chord: Try omitting fifth if chord is in root position
			if rm.inversion() == 0:
				# double root
				if rm.root().name != LT:
					yield from self._generate_voicings([rm.root().name]*2 + [rm.third.name, rm.seventh.name])
				# double third
				if rm.third.name != LT:
					yield from self._generate_voicings([rm.root().name, rm.seventh.name] + [rm.third.name]*2)
		else:
			# TRIAD
			chordMembers = [pitch.name for pitch in rm.pitches]
			if rm.inversion() == 2:
				# 64 chord must double fifth
				yield from self._generate_voicings(chordMembers + [rm.fifth.name])
			else:
				# double root
				if rm.root().name != LT:
					yield from self._generate_voicings(chordMembers + [rm.root().name])
					# Incomplete chord: Try omitting fifth if chord is in root position
					if rm.inversion() == 0:
						# tripled root
						yield from self._generate_voicings([rm.root().name]*3 + [rm.third.name])
						# doubled root and doubled third
						if rm.third.name != LT:
							yield from self._generate_voicings([rm.root().name]*2 + [rm.third.name]*2)
				# double third
				if rm.third.name != LT:
					yield from self._generate_voicings(chordMembers + [rm.third.name])
				# double fifth
				if rm.fifth.name != LT:
					yield from self._generate_voicings(chordMembers + [rm.fifth.name])

	def chordCost(self, chord, rm, last_chord=False):
		"""This method computes the cost of chord voicing infractions
		   and is run once on every chord.
		   Its purpose is to encourage some voicings over others."""

		# Q: why do this separately from voiceChord()? A: for convenience ofc.

		# Note to reader: this function should only discriminate between the different voicings of a particular chord
		# as the chord itself has already been decided.
		cost = 0
		
		# encourage full chord voicings, prefer root doubling.
		if rm.containsSeventh(): # SEVENTH CHORD
			# integrity check: (require root, 3rd, & 7th) ---Optimization: Temporarily Disabled---
			# if not all((p in chord.pitchClasses for p in (rm.root().pitchClass, rm.third.pitchClass, rm.seventh.pitchClass))): #or not 3 <= len(set(chord.pitchClasses)) <= 4:
			# 	cost += 1e7

			# preference checks:
			if len(set(chord.pitchClasses)) < 4:
				if chord.pitchClasses.count(rm.root().pitchClass) == 2:
					cost += 1 # doubled root (and leaving out the fifth)
				else:
					cost += 5 # doubled third
		else: # TRIAD
			# integrity check: (require root & 3rd) ---Optimization: Temporarily Disabled---
			# if not all((p in chord.pitchClasses for p in (rm.root().pitchClass, rm.third.pitchClass))):
			# 	cost += 1e7
			#preference checks:
			if rm.inversion() != 2:
				if chord.pitchClasses.count(rm.root().pitchClass) < 2:
					# penalty for not doubling root
					cost += 2
			if len(set(chord.pitchClasses)) < 3:
				# incomplete chord should be last chord
				if chord.pitchClasses.count(rm.root().pitchClass) == 3:
					# tripled root
					cost += 1 + (7 * int(not(last_chord)))
				else:
					# doubled root & doubled third (guaranteed: can't triple third, can't have fifth)
					cost += 4 + (4 * int(not(last_chord)))

		# check voice ranges:
		for i in range(4):
			if self.midi_ranges[i][0] <= chord[i].pitch.midi <= self.midi_ranges[i][1]:
				continue
			elif self.midi_ranges[i][2] <= chord[i].pitch.midi <= self.midi_ranges[i][3]:
				cost += 4
			else:
				cost += 1e7 # out of allowable/permissible range

		# slightly prefer authentic cadences (soprano doubles root)
		if last_chord and rm.figure.casefold() == 'i' and chord[3].pitch.name != rm.root().name:
			cost += 8
			if (chord[3].pitch.name == rm.fifth.name):
				cost += 5


		return cost

	# TODO: abstract all the penalty weight values to settings.py
	@staticmethod
	def voiceLeadingCost(chord1, rm1, chord2, rm2):
		"""This method computes the costs of voice leading infractions/violations
		   and is run on every adjacent chord pair in a phrase."""
		cost = 0

		# Key of context (in chord1->chord2 resolution/progression)
		if rm1.secondaryRomanNumeral:
			ctx_key = rm1.secondaryRomanNumeralKey
		else:
			ctx_key = rm1.key # Leading Tone

		ctx_scale = ctx_key.getPitches()
		LT = ctx_scale[6].name # ti->do leading tone
		FT = ctx_scale[3].name # fa->mi tendency tone
		# Resolution tones (local variable shortcut optimizations)
		_DO = ctx_scale[0]
		_SOL = ctx_scale[4]
		_MI = ctx_scale[2]

		# functions of chords (in fundamental terms, e.g. viiº42/V/V maps to vii=7)
		func1 = RomanNumeral(rm1.romanNumeralAlone).bassScaleDegreeFromNotation()
		func2 = RomanNumeral(rm2.romanNumeralAlone).bassScaleDegreeFromNotation()

		# make local function: optimization
		above = aboveOctave
		below = belowOctave

		# check ti->(ti or do) (universal, more strict for dominant to tonic resolutions)
		# in middle voices, ti->sol is also acceptable.
		if LT in chord1.pitchNames:
			lt_idx = chord1.pitchNames.index(LT) # leadingtone_index : there can only be one.

			if chord2[lt_idx] not in (chord1[lt_idx], Pitch(_DO, octave=above(_DO, chord1[lt_idx]))): # ti->ti or ti->do have no cost
				if lt_idx == 0 and chord2[lt_idx].name != LT:
					pass # forgive bass if inevitable.
				if lt_idx in {1, 2} and Pitch(_SOL, octave=below(_SOL, chord1[lt_idx])):
					# ti->sol in inner voices (A & T): frustrated leading tone, minimal penalty
					# penalty is more severe for dominant function chord1
					cost += 2 + 2*(func1 in {5,7})
				else:
					cost += 50 + (100*(func1 in {5,7})) * max(10*(func2 in {1,6}), 1)
					# penalty is more severe for dominant function chord1 resolution, especially if it is part of a cadence, i.e. chord2 is I of VI
		
		# fa->mi in dominant(V, viiº)->non-dominant progressions (dominant resolution?)
		if func1 in {5,7} and func2 not in {5,7}:
			if FT in chord1.pitchNames:
				# there might be more than one tendency tone, which is already a violation, but our job here is not to process that.
				# we only want to see if the tencency tones get resolved and add penalties accordingly.
				# (rather inefficient implementation, but easier to read)
				# xxx # ft_idxs = [i for i, p in enumerate(chord1.pitchNames) if p == FT]
				for i, p in enumerate(chord1.pitchNames):
					# dbg(f"hi {i} {p}")
					if p == FT and chord2[i].pitch != Pitch(_MI, octave=below(_MI, chord1[i])): # fa->mi (resolution) is free
						# dbg(f"hi {FT}, {chord2[i]}, {_MI}, {Pitch(_MI, octave=below(_MI, chord1[i]))}")
						if i != 0 or chord2[i].name == _MI.name:
							# negative logic above forgives bass if unable to do 4->3.
							cost += 100 * max(10*(func2 in {1,6}), 1) # penalty increased for cadences.
		
		# Non-dominant seventh is chord1. From PSR apply Suspension or Resolution.
		if func1 not in {5,7} and rm1.containsSeventh():
			# seventh either resolves down one scale degree, or stays in place.
			# for our purposes, (since chord order is not to be checked here), let's consider the resolution proper if it goes down a diatonic second.
			seventh = chord1.seventh
			seven_idx = chord1.pitches.index(seventh) # (seventh cannot be doubled, so is unique)
			# Resolutions have to go down a m2 or M2.
			if not (seventh == chord2[seven_idx] or Interval(noteStart=chord2[seven_idx], noteEnd=chord1[seven_idx]).name in {'m2','M2'}):
				if seven_idx != 0 or (seventh.pitchClass + 12 - chord2[seven_idx].pitchClass)%12 > 2:
					# negative logic above: we forgive the bass if it physically can't suspend {0}/resolve {1,2}
					cost += 60
			# alternative implementation: ================ (that allows enharmonic respellings, but resolutions still have to go down m2 or M2)
			# if not (chord1[seven_idx].pitch.midi == chord2[seven_idx].pitch.midi or (Interval(noteStart=chord2[seven_idx], noteEnd=chord1[seven_idx]).generic.value == 2 and 0 < chord1[seven_idx].pitch.midi - chord2[seven_idx].pitch.midi <= 2)):
			#    cost += 50
		
		# Non-dom 7th is chord2. From PSR apply Preparation into Suspension.
		if func2 not in {5,7} and rm2.containsSeventh():
			seventh = chord2.seventh
			seven_idx = chord2.pitches.index(seventh)
			# does not allow enharmonic equivalent preparation.
			if chord1[seven_idx] != seventh and (seventh_idx != 0 or chord1[seventh_idx].name == seventh.name):
				# negative logic above forgives inevitable bass
				cost += 20
			# Alternative implementation: ================ (allows enharmonic respellings)
			# if chord1[seven_idx].pitch.midi != chord2[seven_idx].pitch.midi:
			#    cost += 20

		# Cross overlap of voices (40$ per crossing): 0=B, 1=T, 2=A, 3=S
		# Q: do we need to convert these note objects to pitch/midi_values? they seem to work fine for now.
		cost += 40 * ((chord1[0] > chord2[1]) + (chord1[1] < chord2[0])
					+ (chord1[1] > chord2[2]) + (chord1[2] < chord2[1])
					+ (chord1[2] > chord2[3]) + (chord1[3] < chord2[2]))

		# Avoid big leaps (generally). Octave leaps in bass is ok. Extra penalty for dissonant leaps
		# note: to register dissonant leaps, use intervals instead of midi-semitone-distance.
		# TODO: is it possible to define heuristics in which big leaps are ok/good?
		# (size of interval-disregarding direction, bool: is dissonant interval[leap])
		diffs = [ (abs(i.generic.value), i.specifier not in {Specifier.PERFECT, Specifier.MAJOR, Specifier.MINOR}) for i in (Interval(noteStart=chord1[i], noteEnd=chord2[i]) for i in range(4)) ]
		# each line: penalty for skips + penalty for dissonances... for a each voice
		cost += ((0 if diffs[0][0] <= 5 or diffs[0][0] == 8 else 20 if diffs[0][0] < 8 else 100) + 50 * diffs[0][1] # Bass
				+ (0 if diffs[1][0] <= 2 else 2 if diffs[1][0] == 3 else 10 if diffs[1][0] <= 5 else 20 if diffs[1][0] <= 8 else 100) + 50 * diffs[1][1] # Tenor
				+ (0 if diffs[2][0] <= 2 else 2 if diffs[2][0] == 3 else 10 if diffs[2][0] <= 5 else 20 if diffs[2][0] <= 8 else 100) + 50 * diffs[2][1] # Alto
				+ (0 if diffs[3][0] <= 2 else 2 if diffs[3][0] == 3 else 15 if diffs[3][0] <= 5 else 30 if diffs[3][0] <= 8 else 150) + 100 * diffs[3][1]) # Soprano
		
		if diffs[0][0] == 8 and Interval(noteStart=chord1[0], noteEnd=chord2[0]).direction.value == 1:
			# prefer bass leaping down octave over bass leaping up. (more natural sounding especially in cadences)
			cost += 5

		# Parallelisms
		# Q: Should we use semitone-distance or intervallic comparison?
		# Anyway, here's the semitone-distance implementation (warning: the items in chord object are notes, not pitches. They require an extra conversion.)
		for i in range(3): # the i=3 (range(4)) case is degenerate.
			i1, i2 = chord1[i].pitch.midi, chord2[i].pitch.midi
			if i1 == i2: # no need to check for oblique motion. More strict way to state this (potentially) is chord[i]==chord[j]
				continue
			for j in range(i+1, 4):
				j1, j2 = chord1[j].pitch.midi, chord2[j].pitch.midi
				# Parallel or Contrary fifths or octaves check.
				if (j1-i1)%12 == (j2-i2)%12 and (j1-i1)%12 in {0, 7}:
					cost += 100 + 100*int(i==0 and j==3) # extra penalty for outer voices.
				# Unequal 5ths. Bass & another voice has a º5 -> P5.
				if i == 0 and j1 != j2 and (j1-i1)%12==6 and (j2-i2)%12==7:
					cost += 75 + 75*int(j==3)
		# direct (hidden) fifths/8ves: Outer voices move in similar motion into P5 or P8 and soprano has a leap.
		# (1) also encourage contrary motion between bass and soprano via a small penalty for similar motion.
		# (2) also encourage interesting melody by penalizing static soprano
		ps1, ps2, pb1, pb2 = chord1[3].pitch.midi, chord2[3].pitch.midi, chord1[0].pitch.midi, chord2[0].pitch.midi
		if abs(ps2-ps1) > 2 and (ps2-pb2)%12 in {0,7}:
			# direct fifths/8ves
			cost += 80
		if ps2 == ps1:
			cost += 5 # task (2)
		if Interval(noteStart=chord1[3], noteEnd=chord2[3]).direction.value * Interval(noteStart=chord1[0], noteEnd=chord2[0]).direction.value == 1:
			# punish similar motion. Descending = -1, Oblique = 0, Ascending = +1. Only similar motion yields a final product of +1.
			cost += 2 # task (1)

		# Special case: if chord 1 and chord 2 are the same, force the upper voices to change up.
		# Optimization Note: chained conitions should short circuit on false subcondition.
		if rm1 == rm2 and chord1[1] == chord2[1] and chord1[2] == chord2[2] and chord1[3] == chord2[3]:
			cost += 50
				
		return cost

	def voicePhraseDP(self, phrase): # phrase: one phrase of roman numerals (presumably with some sort of cadence)
		# stc = 3 # store top choices (not yet implemented, mess around with other pathfinding algs and optimize)

		# O(L^2 N), L = max number of voicings per chord (~120), N = number of chords in phrase.
		# the most expensive operation is progressionCost

		L = len(phrase)
		V = [list(self.voiceChord(rm)) for rm in phrase]
		DP = [[None for _ in range(len(V[i]))] for i in range(L)]
		Mask = [[True for _ in range(len(V[i]))] for i in range(L)] # DP MASK

		# localizing class variables: optimization
		_confidence = self.confidence
		_buffer = self.buffer
		
		# first layer i=0, only chord cost, and no back reference.
		dbg(f"DP: Setting up first chord...")
		for j in range(len(V[0])):
			DP[0][j] = (self.chordCost(V[0][j], phrase[0]), None)
		# Mask updating (pruning)
		# pruning first chord options greatly decrease bottleneck during second chord
		bar = _confidence * ( min(DP[0])[0] + self.first_chord_buffer//(len(V[0])*len(V[1])) )
		for j in range(len(V[0])):
			if DP[0][j][0] > bar:
				Mask[0][j] = False

		# subsequent layers i=1..L-1
		for i in range(1, L):
			dbg_temp_count = sum(Mask[i-1]) #### temp
			dbg(f"DP: running {i+1}(th) chord (of {L} total)... (({dbg_temp_count} of {len(V[i-1])})x{len(V[i])}={dbg_temp_count*len(V[i])} pairs to run)")
			start_time = time.time()

			for j in range(len(V[i])):
				# Note: chord cost of current voicing added at the end.
				best = (1e9, None) # (totalCost, backReference)
				for k in range(len(V[i-1])):
					if not Mask[i-1][k]:
						continue
					current_cost = DP[i-1][k][0] + self.voiceLeadingCost(V[i-1][k], phrase[i-1], V[i][j], phrase[i]) # previous_cost + progression cost
					if current_cost < best[0]:
						best = (current_cost, k)

				if i+1 == L:
					DP[i][j] = (best[0] + self.chordCost(V[i][j], phrase[i], last_chord=True), best[1])
				else:
					DP[i][j] = (best[0] + self.chordCost(V[i][j], phrase[i]), best[1])

			# Mask updating (pruning)
			bar = _confidence * (min(DP[i])[0] + _buffer) 
			for j in range(len(V[i])):
				if DP[i][j][0] > bar:
					Mask[i][j] = False
			
			total_time = time.time() - start_time
			dbg(f"DP:         took {total_time} seconds total ({total_time/(dbg_temp_count*len(V[i]))}) seconds per pair)")

		# retrace the best solution
		dbg(f"DP: Done! Retracing solution...")
		op = DP[-1].index(min(DP[-1])) # op (optimal): always points to the optimal choice at phrase[i].
		dbg(f"Total Cost: {DP[-1][op][0]}")
		sol = []
		for i in reversed(range(L)):
			sol.append(Chord(V[i][op], lyric=phrase[i].figure))
			op = DP[i][op][1] # set op to op's backreference (to the last optimal element)

		sol.reverse()
		return sol


# Note: RomanNumeral.figure

def parseProgression(prog): # chord progression: str
	prog = [l.strip().split(":") for l in prog.split("\n") if l.strip()]
	phrases = []
	for key, chords in prog:
		phrase_key = Key(key)
		phrases.append([RomanNumeral(chord, phrase_key) for chord in filter(None, chords.split())])
	return phrases


# credits to Eric Zhang @ github
def generateScore(chords, lengths=None, ts="4/4"):
    """Generates a four-part score from a sequence of chords.
    Soprano and alto parts are displayed on the top (treble) clef, while tenor
    and bass parts are displayed on the bottom (bass) clef, with correct stem
    directions.
    """
    if lengths is None:
        lengths = [1 for _ in chords]
    else: 
    	while len(lengths) < len(chords):
    		lengths.extend(lengths) # lengths is a pattern
    voices = [Voice([Piano()]) for _ in range(4)]
    for chord, length in zip(chords, lengths):
        bass, tenor, alto, soprano = [
            Note(p, quarterLength=length) for p in chord.pitches
        ]
        bass.addLyric(chord.lyric)
        bass.stemDirection = alto.stemDirection = "down"
        tenor.stemDirection = soprano.stemDirection = "up"
        voices[0].append(soprano)
        voices[1].append(alto)
        voices[2].append(tenor)
        voices[3].append(bass)

    SA = Part([TrebleClef(), TimeSignature(ts), voices[0], voices[1]])
    TB = Part([BassClef(), TimeSignature(ts), voices[2], voices[3]])
    score = Score([SA, TB])
    return score


def generateChorale(chorale, lengths=None, ts="4/4"):
	phrases = parseProgression(chorale)
	engine = SATB()
	chord_progression = []
	i = 0 ####
	ttt = time.time()
	dbg(f"##### CONFIDENCE VALUE = {engine.confidence}")
	for phrase in phrases:
		tt = time.time()
		i += 1 ####
		dbg(f"PHRASE {i} ====================")
		chord_progression.extend(engine.voicePhraseDP(phrase))
		dbg(f"PHRASE TOTAL TIME: {time.time() - tt} seconds")

	dbg(f"++++++++++ TOTAL TIME ++++++++++ {time.time() - ttt} seconds ++++++++++")

	score = generateScore(chord_progression, lengths=lengths, ts=ts)
	score.show()
	return chord_progression




# Extra Utils
def chordProgressionToText(cp):
	print("+S+ +A+ +T+ +B+")
	for chord in cp:
		print("{} {} {} {}".format(chord[3].nameWithOctave, chord[2].nameWithOctave, chord[1].nameWithOctave, chord[0].nameWithOctave))


def textToChordProgression(txt):
	pass


# Unit Tests (temp)
x = SATB()
ar = RomanNumeral('V7','C')
br = RomanNumeral('I','C')
a = list(x.voiceChord(ar))
b = list(x.voiceChord(br))
print(f"a len: {len(a)}, b len: {len(b)}")
ch = """D: I vi I6 IV I64 V I
 D: I6 V64 I IV6 V I6 V
 D: I IV6 I6 IV I64 V7 vi
 D: I6 V43 I I6 ii65 V I
 A: I IV64 I vi ii6 V7 I
 b: iv6 i64 iv iio6 i64 V7 i
 A: IV IV V I6 ii V65 I
 D: IV6 I V65 I ii65 V7 I"""
chsh = "D: I IV V V7 I"

