/*
  Crazyhouse-Stockfish release version

  Development source remains explicitly unversioned until the exact stable
  candidate has passed the final strength and release gates. The stable commit
  changes all four constants together before its exact-binary panel.
*/

#ifndef CRAZYHOUSE_VERSION_H_INCLUDED
#define CRAZYHOUSE_VERSION_H_INCLUDED

#include <string_view>

namespace Stockfish {

inline constexpr int CrazyhouseVersionMajor = 0;
inline constexpr int CrazyhouseVersionMinor = 0;
inline constexpr int CrazyhouseVersionPatch = 0;

inline constexpr std::string_view CrazyhouseVersionString = "dev";

}  // namespace Stockfish

#endif  // CRAZYHOUSE_VERSION_H_INCLUDED
