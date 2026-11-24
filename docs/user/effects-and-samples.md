# Effects and the sample project

## Try FacadeKit without any hardware

You do not need a projector, a camera or a Raspberry Pi to see what FacadeKit does.

1. Start FacadeKit (`facadekit serve`).
2. On **Projects**, under **Try a sample project**, choose a theme (Halloween or Winter) and
   press **Try a sample project**.
3. Wait a few seconds. FacadeKit scans a **simulated house**, marks its garage door and
   two windows, and lays out a 12-second show.

You should see: the sample open in **Arrange**, with the show playing in the preview.

From a terminal you can make the same project with:

```bash
facadekit new my-sample.fkproj --sample winter
facadekit render my-sample.fkproj
```

The house in a sample is not real. Its scan, door and windows come from FacadeKit's
simulator, and the project name and the title card say "simulated house". To light your own
house, make a new project and scan it (see the [quick start](quick-start.md)).

Everything in a sample is made by FacadeKit itself: built-in effects, a title card and a
drawn pumpkin or snowflake. There are no downloaded files, and you may project a sample
anywhere.

## Effects

An effect is a clip that needs no media file. In **Arrange**, add a clip, choose **Effect**
as its source, then pick one:

| Effect | What it does |
|---|---|
| Solid colour | One colour |
| Colour wash | Two colours sliding across the surface |
| Outline trace | A light running round the edge with a tail |
| Marching border | Dashes marching round the edge |
| Pulsing rings | Rings spreading from the middle |
| Light sweep | A soft bar of light crossing the surface |
| Colour cycle | A rainbow drifting across the surface |
| Plasma waves | Two colours flowing like liquid |
| Starfield | Twinkling stars; **Seed** picks a different sky |
| Snowfall | Falling, drifting flakes; **Seed** picks different flakes |
| Gentle flicker | A candle-like flicker, at most 3 changes a second |
| Strobe | Hard flashes |

The same settings always give the same picture, in the preview and in the render. Change
**Seed** to get a different starfield, snowfall or flicker.

### Flashing and photosensitive epilepsy

Flashing light faster than about 3 times a second can trigger seizures in people with
photosensitive epilepsy, and people passing a house cannot look away. **Gentle flicker**
never changes more than 3 times a second. **Strobe** can go faster; FacadeKit shows a
warning when it is set above 3 Hz. Keep strobes at 3 Hz or less on a show people walk past.
