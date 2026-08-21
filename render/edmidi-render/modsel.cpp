/*
 * Module selection for edmidi-render (C++11; the library itself is gnu++98 but the
 * class layouts do not depend on the standard level).
 *
 * libEDMIDI exposes no API to choose chips: CSMFPlay's constructor attaches an
 * OPLL device to every even module and an SCC device to every odd one, and the
 * module array is a private member. To render "OPLL only" or "SCC only" we
 * reach into the player object (same headers the library was compiled with, so
 * the layout is identical) and replace the unwanted devices with a silent one.
 * This file is the only place that depends on libEDMIDI internals; it is built
 * against the library's own src/ tree in the render image.
 */
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <string>
#include <vector>

#include "emu_de_midi.h"
#include "ISoundDevice.hpp"
#include "CMIDIModule.hpp"     /* pulled in normally first: pl_list.hpp uses `template<class T>` */

/* CSMFPlay declares its members in the implicit-private section of a `class`, so the
 * usual `#define private public` does nothing; turning the class-key into `struct` makes
 * the default access public. Only CSMFPlay.hpp's own declarations are affected (every
 * header it includes is already guarded above). The layout is unchanged. */
#define class struct
#include "CSMFPlay.hpp"
#undef class

namespace {

class NullDevice : public dsa::ISoundDevice
{
public:
    NullDevice() {}
    virtual ~NullDevice() {}
    virtual const dsa::SoundDeviceInfo &GetDeviceInfo(void) const
    {
        static dsa::SoundDeviceInfo si;
        si.max_ch = 9;                           /* same voice count as the OPLL: keeps the allocator happy */
        si.version = 0x0001;
        si.name = (BYTE *)"NULL";
        si.desc = (BYTE *)"silent placeholder";
        return si;
    }
    virtual dsa::RESULT Reset(void) { return dsa::SUCCESS; }
    virtual dsa::RESULT Render(INT32 buf[2]) { buf[0] = 0; buf[1] = 0; return dsa::SUCCESS; }
    virtual void SetProgram(UINT, UINT8, UINT8) {}
    virtual void SetVelocity(UINT, UINT8) {}
    virtual void SetPan(UINT, UINT8) {}
    virtual void SetVolume(UINT, UINT8) {}
    virtual void SetBend(UINT, INT8, INT8) {}
    virtual void KeyOn(UINT, UINT8) {}
    virtual void KeyOff(UINT) {}
    virtual void PercKeyOn(UINT8) {}
    virtual void PercKeyOff(UINT8) {}
    virtual void PercSetProgram(UINT8, UINT8) {}
    virtual void PercSetVelocity(UINT8, UINT8) {}
    virtual void PercSetVolume(UINT8) {}
};

} // namespace

extern "C" int edmidi_render_select(struct EDMIDIPlayer *dev, const char *mode, int modules)
{
    if (!dev || !dev->edmidiPlayer || !mode)
        return -1;
    if (!std::strcmp(mode, "all"))
        return 0;

    int keep_parity;
    if (!std::strcmp(mode, "opll"))
        keep_parity = 0;        /* even modules are OPLL */
    else if (!std::strcmp(mode, "scc"))
        keep_parity = 1;        /* odd modules are SCC */
    else
        return -1;

    dsa::CSMFPlay *play = static_cast<dsa::CSMFPlay *>(dev->edmidiPlayer);
    if (play->m_mods != modules || modules < 2 || modules > 16)
        return -1;              /* layout sanity check: m_mods must read back what edmidi_initEx was given */
    for (int i = 0; i < play->m_mods; i++)
    {
        if ((i & 1) == keep_parity)
            continue;
        dsa::ISoundDevice *old = play->m_module[i].DetachDevice();
        delete old;
        play->m_module[i].AttachDevice(new NullDevice());
    }
    return 0;
}
